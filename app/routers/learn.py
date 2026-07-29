"""学习系统路由 - 文案结构学习 + 按学习模板剪辑

两条独立流程：
  1. 学习：POST /{video_id}/analyze  -> LLM 分析文案角色 -> POST /templates 保存模板
  2. 应用：POST /{video_id}/clip-by-template/{tpl_id} -> 按模板删除低价值角色
"""
import os
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.config import config
from app.database import Video, Transcript, ClipResult, LearningTemplate, SessionLocal
from app.services.llm_client import LLMClient
from app.services.script_analyzer import ScriptAnalyzer
from app.services.clipper import clipper
from app.services.silence_detector import ranges_to_dict_list, complement_ranges
from app.services.keyword_filter import merge_ranges, segments_to_keep_ranges

router = APIRouter(prefix="/api/learn", tags=["learning"])

# 全局实例（复用连接）
_llm = LLMClient()
_analyzer = ScriptAnalyzer(_llm)


# ─── 学习：分析文案结构 ───

@router.post("/{video_id}/analyze")
async def analyze_script(video_id: str):
    """分析视频的文案结构 - LLM 给每段语音打角色标签

    前置：视频已完成转写
    返回：分析结果 + 模板建议（哪些角色删除/保留）
    """
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        if not t or not t.segments:
            raise HTTPException(400, "请先完成语音转写")
        segments = t.segments
        video_name = v.filename
    finally:
        db.close()

    def _do():
        awaitable = _analyzer.analyze_segments(segments)
        loop = asyncio.new_event_loop()
        try:
            analyzed = loop.run_until_complete(awaitable)
        finally:
            loop.close()

        # 生成模板建议
        loop2 = asyncio.new_event_loop()
        try:
            tpl_config = loop2.run_until_complete(_analyzer.generate_template_config(analyzed))
        finally:
            loop2.close()

        return analyzed, tpl_config

    analyzed, tpl_config = await asyncio.to_thread(_do)

    return {
        "video_id": video_id,
        "video_name": video_name,
        "segment_count": len(analyzed),
        "analyzed_segments": analyzed,
        "template_suggestion": tpl_config,
    }


# ─── 模板管理 ───

class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    source_video_id: Optional[str] = None
    delete_roles: List[str]
    keep_roles: List[str] = []
    role_stats: dict = {}
    analyzed_segments: List[dict] = []


@router.get("/templates")
async def list_templates():
    """列出所有学习模板"""
    db = SessionLocal()
    try:
        templates = db.query(LearningTemplate).order_by(
            LearningTemplate.created_at.desc()
        ).all()
        return [{
            "id": t.id,
            "name": t.name,
            "description": t.description or "",
            "source_video_id": t.source_video_id,
            "delete_roles": t.delete_roles or [],
            "keep_roles": t.keep_roles or [],
            "segment_count": len(t.analyzed_segments or []),
            "created_at": t.created_at or "",
        } for t in templates]
    finally:
        db.close()


@router.get("/templates/{tpl_id}")
async def get_template(tpl_id: str):
    """获取模板详情"""
    db = SessionLocal()
    try:
        t = db.query(LearningTemplate).filter(LearningTemplate.id == tpl_id).first()
        if not t:
            raise HTTPException(404, "模板不存在")
        return {
            "id": t.id,
            "name": t.name,
            "description": t.description or "",
            "source_video_id": t.source_video_id,
            "delete_roles": t.delete_roles or [],
            "keep_roles": t.keep_roles or [],
            "role_stats": t.role_stats or {},
            "analyzed_segments": t.analyzed_segments or [],
            "created_at": t.created_at or "",
        }
    finally:
        db.close()


@router.post("/templates")
async def create_template(params: TemplateCreate):
    """保存学习模板"""
    if not params.name:
        raise HTTPException(400, "模板名称不能为空")
    if not params.delete_roles:
        raise HTTPException(400, "请至少选择一个要删除的角色")

    db = SessionLocal()
    try:
        tpl = LearningTemplate(
            name=params.name,
            description=params.description,
            source_video_id=params.source_video_id,
            delete_roles=params.delete_roles,
            keep_roles=params.keep_roles,
            role_stats=params.role_stats,
            analyzed_segments=params.analyzed_segments,
        )
        db.add(tpl)
        db.commit()
        db.refresh(tpl)
        return {
            "id": tpl.id,
            "name": tpl.name,
            "message": "模板保存成功",
        }
    finally:
        db.close()


@router.put("/templates/{tpl_id}")
async def update_template(tpl_id: str, params: TemplateCreate):
    """更新模板（调整删除/保留角色）"""
    db = SessionLocal()
    try:
        t = db.query(LearningTemplate).filter(LearningTemplate.id == tpl_id).first()
        if not t:
            raise HTTPException(404, "模板不存在")
        t.name = params.name
        t.description = params.description
        t.delete_roles = params.delete_roles
        t.keep_roles = params.keep_roles
        if params.role_stats:
            t.role_stats = params.role_stats
        if params.analyzed_segments:
            t.analyzed_segments = params.analyzed_segments
        t.updated_at = datetime.now(timezone.utc).isoformat()
        db.commit()
        return {"message": "更新成功", "id": t.id}
    finally:
        db.close()


@router.delete("/templates/{tpl_id}")
async def delete_template(tpl_id: str):
    """删除模板"""
    db = SessionLocal()
    try:
        t = db.query(LearningTemplate).filter(LearningTemplate.id == tpl_id).first()
        if not t:
            raise HTTPException(404, "模板不存在")
        db.delete(t)
        db.commit()
        return {"message": "删除成功"}
    finally:
        db.close()


# ─── 应用：按学习模板剪辑 ───

class ClipByTemplateParams(BaseModel):
    template_id: str
    margin: float = 0.2          # 段落前后留白
    also_remove_silence: bool = False  # 同时去静音
    noise_db: Optional[float] = None
    min_duration: Optional[float] = None


@router.post("/{video_id}/clip-by-template")
async def clip_by_template(video_id: str, params: ClipByTemplateParams):
    """按学习模板剪辑视频

    流程：转写段落 -> LLM 角色分类 -> 删除模板标记的角色 -> 输出
    """
    db = SessionLocal()
    try:
        v = db.query(Video).filter(Video.id == video_id).first()
        if not v:
            raise HTTPException(404, "视频不存在")
        filepath = v.filepath
        duration = v.duration
        t = db.query(Transcript).filter(Transcript.video_id == video_id).first()
        if not t or not t.segments:
            raise HTTPException(400, "请先完成语音转写")
        segments = t.segments
        tpl = db.query(LearningTemplate).filter(
            LearningTemplate.id == params.template_id
        ).first()
        if not tpl:
            raise HTTPException(404, "模板不存在")
        delete_roles = tpl.delete_roles or []
    finally:
        db.close()

    def _do():
        # 1. LLM 分析文案角色
        loop = asyncio.new_event_loop()
        try:
            analyzed = loop.run_until_complete(_analyzer.analyze_segments(segments))
        finally:
            loop.close()

        # 2. 按模板删除指定角色
        keep_segs = []
        removed_segs = []
        for s in analyzed:
            if s.get("role") in delete_roles:
                removed_segs.append(s)
            else:
                keep_segs.append(s)

        # 3. 转为保留区间
        keep_ranges_raw = segments_to_keep_ranges(keep_segs, params.margin)
        keep_ranges = merge_ranges(keep_ranges_raw)

        # 4. 可选：同时去静音
        from app.services.silence_detector import detect_silence
        from app.services.clipper import intersect_ranges
        if params.also_remove_silence:
            noise_db = params.noise_db if params.noise_db is not None else config.silence.noise_db
            min_dur = params.min_duration if params.min_duration is not None else config.silence.min_duration
            silence_ranges, _ = detect_silence(filepath, noise_db, min_dur)
            keep_ranges = intersect_ranges(
                keep_ranges,
                complement_ranges(silence_ranges, duration)
            )

        if not keep_ranges:
            raise ValueError("删除后无保留内容，请调整模板角色")

        # 5. 剪辑
        out = clipper.clip_by_ranges(
            filepath, keep_ranges,
            f"{video_id}_学习剪辑_{uuid.uuid4().hex[:6]}",
        )

        # 6. 记录结果
        removed_ranges = complement_ranges(keep_ranges, duration)
        db2 = SessionLocal()
        try:
            cr = ClipResult(
                video_id=video_id, clip_type="learn",
                keep_ranges=ranges_to_dict_list(keep_ranges),
                removed_ranges=ranges_to_dict_list(removed_ranges),
                output_path=out,
                params={
                    "template_id": params.template_id,
                    "template_name": tpl.name,
                    "delete_roles": delete_roles,
                    "removed_segment_roles": [s.get("role") for s in removed_segs],
                    "margin": params.margin,
                    "also_remove_silence": params.also_remove_silence,
                },
            )
            db2.add(cr)
            db2.commit()
            db2.refresh(cr)
            return {
                "id": cr.id,
                "output_path": out,
                "keep_count": len(keep_ranges),
                "removed_count": len(removed_ranges),
                "removed_segments": len(removed_segs),
                "kept_segments": len(keep_segs),
                "role_distribution": _count_roles(analyzed),
            }
        finally:
            db2.close()

    result = await asyncio.to_thread(_do)
    return result


def _count_roles(analyzed: List[dict]) -> dict:
    """统计角色分布"""
    counts = {}
    for s in analyzed:
        r = s.get("role", "content")
        counts[r] = counts.get(r, 0) + 1
    return counts
