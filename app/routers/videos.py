"""视频处理路由"""
import os
import uuid
import shutil
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from app.config import config
from app.database import Video, Transcript, ClipResult, SessionLocal
from app.services.clipper import clipper, intersect_ranges
from app.services.transcriber import transcriber
from app.services.silence_detector import detect_silence, Range, ranges_to_dict_list, complement_ranges, randomize_removed_ranges
from app.services.keyword_filter import filter_by_keywords, merge_ranges, segments_to_keep_ranges

router = APIRouter(prefix="/api/videos", tags=["videos"])

ALLOWED_VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts"}
ALLOWED_SUB = {".srt", ".vtt"}


# ─── 上传 / 列表 / 详情 / 删除 ───

@router.get("")
async def list_videos():
    db = SessionLocal()
    try:
        videos = db.query(Video).order_by(Video.created_at.desc()).all()
        return [{
            "id": v.id, "filename": v.filename, "duration": v.duration,
            "status": v.status,
            "created_at": v.created_at or "",
        } for v in videos]
    finally:
        db.close()


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "未选择文件")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_VIDEO:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    video_id = str(uuid.uuid4())
    upload_dir = Path(config.paths.uploads)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / f"{video_id}{ext}"

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    duration = await asyncio.to_thread(clipper.get_video_duration, str(filepath))

    db = SessionLocal()
    try:
        video = Video(id=video_id, filename=file.filename,
                      filepath=str(filepath), duration=duration, status="uploaded")
        db.add(video)
        db.commit()
    finally:
        db.close()

    return {"id": video_id, "filename": file.filename, "duration": duration}


@router.get("/{video_id}")
async def get_video(video_id: str):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        return {
            "id": v.id, "filename": v.filename, "filepath": v.filepath,
            "duration": v.duration, "status": v.status,
            "created_at": v.created_at or "",
        }
    finally:
        db.close()


@router.delete("/{video_id}")
async def delete_video(video_id: str):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        # 删源文件
        if os.path.exists(v.filepath):
            os.remove(v.filepath)
        # 删剪辑产物
        for cr in db.query(ClipResult).filter(ClipResult.video_id == video_id).all():
            if cr.output_path and os.path.exists(cr.output_path):
                os.remove(cr.output_path)
        # 删临时文件
        for f in Path(config.paths.uploads).glob(f"{video_id}*"):
            f.unlink(missing_ok=True)
        # 删数据库
        db.query(ClipResult).filter(ClipResult.video_id == video_id).delete()
        db.query(Transcript).filter(Transcript.video_id == video_id).delete()
        db.delete(v)
        db.commit()
        return {"message": "删除成功"}
    finally:
        db.close()


# ─── 转写 ───

def _do_transcribe(video_id: str):
    try:
        db = SessionLocal()
        v = db.query(Video).filter(Video.id == video_id).first()
        filepath = v.filepath
        db.close()

        audio_path = str(Path(config.paths.uploads) / f"{video_id}.wav")
        transcriber.extract_audio(filepath, audio_path)
        transcriber.transcribe(video_id, audio_path)
        if os.path.exists(audio_path):
            os.remove(audio_path)

        db = SessionLocal()
        v = db.query(Video).filter(Video.id == video_id).first()
        if v:
            v.status = "transcribed"
            v.updated_at = datetime.now(timezone.utc).isoformat()
            db.commit()
        db.close()
    except Exception as e:
        import traceback; traceback.print_exc()
        db = SessionLocal()
        v = db.query(Video).filter(Video.id == video_id).first()
        if v:
            v.status = "error"
            db.commit()
        db.close()


@router.post("/{video_id}/transcribe")
async def start_transcribe(video_id: str):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        if v.status in ("transcribing", "processing"):
            raise HTTPException(400, "正在处理中")
        v.status = "transcribing"
        db.commit()
    finally:
        db.close()
    asyncio.get_event_loop().run_in_executor(None, _do_transcribe, video_id)
    return {"message": "转写已开始", "video_id": video_id}


@router.post("/{video_id}/upload-subtitle")
async def upload_subtitle(video_id: str, file: UploadFile = File(...)):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
    finally:
        db.close()

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_SUB:
        raise HTTPException(400, f"不支持的字幕格式: {ext}")

    sub_path = Path(config.paths.uploads) / f"{video_id}_sub{ext}"
    with open(sub_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        transcriber.transcribe_from_subtitle(video_id, str(sub_path))
    except Exception as e:
        raise HTTPException(500, f"字幕解析失败: {e}")
    finally:
        sub_path.unlink(missing_ok=True)

    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if v:
            v.status = "transcribed"
            v.updated_at = datetime.now(timezone.utc).isoformat()
            db.commit()
    finally:
        db.close()
    return {"message": "字幕导入成功"}


@router.get("/{video_id}/transcript")
async def get_transcript(video_id: str):
    db = SessionLocal()
    try:
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        if not t:
            raise HTTPException(404, "暂无字幕，请先转写")
        return {
            "segments": t.segments or [],
            "full_text": t.full_text or "",
            "language": t.language,
        }
    finally:
        db.close()


# ─── 剪辑：静音删除 ───

class SilenceParams(BaseModel):
    noise_db: Optional[float] = None
    min_duration: Optional[float] = None
    random_delete: bool = False
    min_ratio: float = 0.5
    max_ratio: float = 1.0


@router.post("/{video_id}/clip-silence")
async def clip_silence(video_id: str, params: SilenceParams):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        filepath = v.filepath
        duration = v.duration
    finally:
        db.close()

    noise_db = params.noise_db if params.noise_db is not None else config.silence.noise_db
    min_dur = params.min_duration if params.min_duration is not None else config.silence.min_duration

    def _do():
        silence_ranges, _ = detect_silence(filepath, noise_db, min_dur)
        if params.random_delete:
            # 随机删除每个静音片段的部分帧，保留剩余
            actual_removed = randomize_removed_ranges(
                silence_ranges, params.min_ratio, params.max_ratio)
            keep_ranges = complement_ranges(actual_removed, duration)
        else:
            actual_removed = silence_ranges
            keep_ranges = complement_ranges(silence_ranges, duration)
        out = clipper.clip_by_ranges(
            filepath, keep_ranges,
            f"{video_id}_去静音_{uuid.uuid4().hex[:6]}",
        )
        # 记录结果
        db2 = SessionLocal()
        try:
            cr = ClipResult(
                video_id=video_id, clip_type="silence",
                keep_ranges=ranges_to_dict_list(keep_ranges),
                removed_ranges=ranges_to_dict_list(actual_removed),
                output_path=out,
                params={"noise_db": noise_db, "min_duration": min_dur,
                        "random_delete": params.random_delete,
                        "min_ratio": params.min_ratio, "max_ratio": params.max_ratio},
            )
            db2.add(cr)
            db2.commit()
            db2.refresh(cr)
            return {"id": cr.id, "output_path": out,
                    "keep_count": len(keep_ranges), "removed_count": len(actual_removed)}
        finally:
            db2.close()

    result = await asyncio.to_thread(_do)
    return result


# ─── 剪辑：关键词删除 ───

class KeywordParams(BaseModel):
    keywords: list[str]
    margin: float = 0.3


@router.post("/{video_id}/clip-keywords")
async def clip_keywords(video_id: str, params: KeywordParams):
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        filepath = v.filepath
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        if not t:
            raise HTTPException(400, "请先完成语音转文字")
        segments = t.segments or []
    finally:
        db.close()

    def _do():
        keep, removed = filter_by_keywords(segments, params.keywords, params.margin)
        keep = merge_ranges(keep)
        if not keep:
            raise ValueError("所有片段均被关键词命中，无保留内容")
        out = clipper.clip_by_ranges(
            filepath, keep,
            f"{video_id}_去关键词_{uuid.uuid4().hex[:6]}",
        )
        db2 = SessionLocal()
        try:
            cr = ClipResult(
                video_id=video_id, clip_type="keyword",
                keep_ranges=[r.to_dict() for r in keep],
                removed_ranges=[r.to_dict() for r in removed],
                output_path=out,
                params={"keywords": params.keywords, "margin": params.margin},
            )
            db2.add(cr)
            db2.commit()
            db2.refresh(cr)
            return {"id": cr.id, "output_path": out,
                    "keep_count": len(keep), "removed_count": len(removed)}
        finally:
            db2.close()

    result = await asyncio.to_thread(_do)
    return result


# ─── 剪辑：删除无文字部分 ───

class NoTextParams(BaseModel):
    margin: float = 0.2
    random_delete: bool = False
    min_ratio: float = 0.5
    max_ratio: float = 1.0


@router.post("/{video_id}/clip-no-text")
async def clip_no_text(video_id: str, params: NoTextParams):
    """删除视频中没有识别到文字的部分（保留有语音内容的片段）"""
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        filepath = v.filepath
        duration = v.duration
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        if not t:
            raise HTTPException(400, "请先完成语音转文字")
        segments = t.segments or []
    finally:
        db.close()

    def _do():
        text_keep = segments_to_keep_ranges(segments, params.margin)
        if not text_keep:
            raise ValueError("未识别到任何文字，无法处理")
        removed = complement_ranges(text_keep, duration)
        if params.random_delete:
            # 随机删除每个无文字片段的部分帧，保留剩余
            actual_removed = randomize_removed_ranges(
                removed, params.min_ratio, params.max_ratio)
            keep = complement_ranges(actual_removed, duration)
        else:
            actual_removed = removed
            keep = text_keep
        out = clipper.clip_by_ranges(
            filepath, keep,
            f"{video_id}_去无文字_{uuid.uuid4().hex[:6]}",
        )
        db2 = SessionLocal()
        try:
            cr = ClipResult(
                video_id=video_id, clip_type="no_text",
                keep_ranges=ranges_to_dict_list(keep),
                removed_ranges=ranges_to_dict_list(actual_removed),
                output_path=out,
                params={"margin": params.margin,
                        "random_delete": params.random_delete,
                        "min_ratio": params.min_ratio, "max_ratio": params.max_ratio},
            )
            db2.add(cr)
            db2.commit()
            db2.refresh(cr)
            return {"id": cr.id, "output_path": out,
                    "keep_count": len(keep), "removed_count": len(removed)}
        finally:
            db2.close()

    result = await asyncio.to_thread(_do)
    return result


# ─── 剪辑：组合（静音 + 关键词）───

class ComboParams(BaseModel):
    keywords: list[str] = []
    noise_db: Optional[float] = None
    min_duration: Optional[float] = None
    margin: float = 0.3
    remove_no_text: bool = False
    no_text_margin: float = 0.2
    random_delete: bool = False
    min_ratio: float = 0.5
    max_ratio: float = 1.0


@router.post("/{video_id}/clip-combo")
async def clip_combo(video_id: str, params: ComboParams):
    """组合剪辑：去静音 + 去无文字 + 去关键词，取交集"""
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        filepath = v.filepath
        duration = v.duration
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        segments = t.segments if t else []
    finally:
        db.close()

    noise_db = params.noise_db if params.noise_db is not None else config.silence.noise_db
    min_dur = params.min_duration if params.min_duration is not None else config.silence.min_duration

    def _do():
        all_removed = []

        # 1. 静音检测
        silence_ranges, _ = detect_silence(filepath, noise_db, min_dur)
        if params.random_delete:
            actual_silence = randomize_removed_ranges(
                silence_ranges, params.min_ratio, params.max_ratio)
        else:
            actual_silence = silence_ranges
        final_keep = complement_ranges(actual_silence, duration)

        # 2. 去无文字（如果启用且有字幕）
        if params.remove_no_text and segments:
            text_keep = segments_to_keep_ranges(segments, params.no_text_margin)
            if params.random_delete:
                text_removed = complement_ranges(text_keep, duration)
                actual_text_removed = randomize_removed_ranges(
                    text_removed, params.min_ratio, params.max_ratio)
                text_keep = complement_ranges(actual_text_removed, duration)
            final_keep = intersect_ranges(final_keep, text_keep)

        # 3. 关键词过滤
        if params.keywords and segments:
            kw_keep, kw_removed = filter_by_keywords(segments, params.keywords, params.margin)
            kw_keep = merge_ranges(kw_keep)
            all_removed = kw_removed
            final_keep = intersect_ranges(final_keep, kw_keep)

        if not final_keep:
            raise ValueError("组合后无保留内容")

        out = clipper.clip_by_ranges(
            filepath, final_keep,
            f"{video_id}_组合_{uuid.uuid4().hex[:6]}",
        )
        db2 = SessionLocal()
        try:
            cr = ClipResult(
                video_id=video_id, clip_type="combo",
                keep_ranges=ranges_to_dict_list(final_keep),
                removed_ranges=ranges_to_dict_list(all_removed),
                output_path=out,
                params={"keywords": params.keywords, "noise_db": noise_db,
                        "min_duration": min_dur, "margin": params.margin,
                        "remove_no_text": params.remove_no_text,
                        "no_text_margin": params.no_text_margin,
                        "random_delete": params.random_delete,
                        "min_ratio": params.min_ratio, "max_ratio": params.max_ratio},
            )
            db2.add(cr)
            db2.commit()
            db2.refresh(cr)
            return {"id": cr.id, "output_path": out,
                    "keep_count": len(final_keep), "removed_count": len(all_removed)}
        finally:
            db2.close()

    result = await asyncio.to_thread(_do)
    return result


# ─── 结果列表 / 下载 ───

@router.get("/{video_id}/results")
async def list_results(video_id: str):
    db = SessionLocal()
    try:
        results = db.query(ClipResult).filter(
            ClipResult.video_id == video_id
        ).order_by(ClipResult.created_at.desc()).all()
        return [{
            "id": r.id, "clip_type": r.clip_type,
            "keep_count": len(r.keep_ranges or []),
            "removed_count": len(r.removed_ranges or []),
            "output_path": r.output_path,
            "filename": os.path.basename(r.output_path) if r.output_path else "",
            "created_at": r.created_at or "",
        } for r in results]
    finally:
        db.close()


@router.get("/{video_id}/download/{result_id}")
async def download_result(video_id: str, result_id: str):
    db = SessionLocal()
    try:
        cr = db.query(ClipResult).filter(
            ClipResult.id == result_id, ClipResult.video_id == video_id
        ).first()
        if not cr or not cr.output_path:
            raise HTTPException(404, "结果不存在")
        if not os.path.exists(cr.output_path):
            raise HTTPException(404, "文件已丢失")
        return FileResponse(
            cr.output_path,
            filename=os.path.basename(cr.output_path),
            media_type="video/mp4",
        )
    finally:
        db.close()
