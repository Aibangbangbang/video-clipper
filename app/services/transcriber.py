"""语音转文字服务 - 基于 faster-whisper 本地转写"""
import os
import uuid
import subprocess
from pathlib import Path

from app.config import config
from app.database import Transcript, SessionLocal


class Transcriber:
    def __init__(self):
        self.model = None

    def _load_model(self):
        if self.model is None:
            self._preload_cuda_libs()
            from faster_whisper import WhisperModel
            print(f"[转写] 加载 Whisper 模型: {config.whisper.model_size} "
                  f"({config.whisper.device}/{config.whisper.compute_type})")
            self.model = WhisperModel(
                config.whisper.model_size,
                device=config.whisper.device,
                compute_type=config.whisper.compute_type,
            )
        return self.model

    @staticmethod
    def _preload_cuda_libs():
        """预加载 CUDA 库（WSL 缺少 libcublas，从 pip 包显式加载）"""
        if config.whisper.device != "cuda":
            return
        import ctypes
        import glob
        nvidia_base = os.path.expanduser("~/.local/lib/python3.10/site-packages/nvidia")
        if not os.path.isdir(nvidia_base):
            return
        # 按依赖顺序加载
        for lib_name in ["libcublasLt.so.12", "libcublas.so.12", "libnvrtc.so.12"]:
            for path in glob.glob(os.path.join(nvidia_base, "*", "lib", lib_name)):
                try:
                    ctypes.CDLL(path)
                    print(f"[转写] 预加载CUDA库: {lib_name}")
                except Exception as e:
                    print(f"[转写] 预加载失败 {lib_name}: {e}")

    def extract_audio(self, video_path: str, audio_path: str):
        """从视频提取 16kHz 单声道 wav（whisper 要求）"""
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn",                    # 不要视频
            "-ac", "1",               # 单声道
            "-ar", "16000",           # 16kHz
            "-f", "wav",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"音频提取失败: {result.stderr[:500]}")
        print(f"[转写] 音频提取完成: {audio_path}")

    def transcribe(self, video_id: str, audio_path: str) -> Transcript:
        """转写音频文件，返回带时间戳的片段列表"""
        model = self._load_model()
        print(f"[转写] 开始转写: {audio_path}")

        segments, info = model.transcribe(
            audio_path,
            language=config.whisper.language,
            vad_filter=True,          # 过滤纯静音段，提升质量
            beam_size=5,
        )

        segment_list = []
        full_text_parts = []

        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            segment_list.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": text,
            })
            full_text_parts.append(text)

        full_text = " ".join(full_text_parts)
        print(f"[转写] 完成: {len(segment_list)} 段, 共 {len(full_text)} 字")

        return self._save_transcript(video_id, segment_list, full_text, info.language)

    def transcribe_from_subtitle(self, video_id: str, subtitle_path: str) -> Transcript:
        """从 SRT/VTT 字幕文件导入（跳过 whisper 转写）"""
        ext = Path(subtitle_path).suffix.lower()
        if ext == ".srt":
            segments = self._parse_srt(subtitle_path)
        elif ext == ".vtt":
            segments = self._parse_vtt(subtitle_path)
        else:
            raise ValueError(f"不支持的字幕格式: {ext}")

        full_text = " ".join(s["text"] for s in segments)
        return self._save_transcript(video_id, segments, full_text, "zh")

    def _parse_srt(self, path: str) -> list:
        segments = []
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for block in content.strip().split("\n\n"):
            lines = block.strip().split("\n")
            if len(lines) >= 3:
                times = lines[1].split(" --> ")
                if len(times) == 2:
                    text = " ".join(lines[2:])
                    segments.append({
                        "start": round(self._parse_time(times[0]), 2),
                        "end": round(self._parse_time(times[1]), 2),
                        "text": text.strip(),
                    })
        return segments

    def _parse_vtt(self, path: str) -> list:
        segments = []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if "-->" in line:
                times = line.split(" --> ")
                if len(times) == 2:
                    parts = []
                    i += 1
                    while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                        parts.append(lines[i].strip())
                        i += 1
                    if parts:
                        segments.append({
                            "start": round(self._parse_time(times[0].replace(".", ",")), 2),
                            "end": round(self._parse_time(times[1].replace(".", ",")), 2),
                            "text": " ".join(parts),
                        })
                    continue
            i += 1
        return segments

    def _parse_time(self, time_str: str) -> float:
        """解析 00:01:23,456 格式"""
        parts = time_str.strip().replace(",", ".").split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        return 0.0

    def _save_transcript(self, video_id, segments, full_text, language) -> Transcript:
        db = SessionLocal()
        try:
            db.query(Transcript).filter(Transcript.video_id == video_id).delete()
            transcript = Transcript(
                video_id=video_id,
                segments=segments,
                full_text=full_text,
                language=language,
            )
            db.add(transcript)
            db.commit()
            db.refresh(transcript)
            return transcript
        finally:
            db.close()

    def get_transcript(self, video_id: str) -> Transcript | None:
        db = SessionLocal()
        try:
            return db.query(Transcript).filter(Transcript.video_id == video_id).first()
        finally:
            db.close()


transcriber = Transcriber()
