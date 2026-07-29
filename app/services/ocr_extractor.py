"""OCR 字幕提取服务 - 从视频画面中提取硬字幕（烧录字幕）

工作流程：
  1. ffmpeg 按固定间隔提取视频帧（只截底部 1/3 区域，字幕通常在此）
  2. RapidOCR 识别每帧文字
  3. 相邻帧相同文字去重合并
  4. 输出带时间戳的字幕段

依赖：rapidocr-onnxruntime, ffmpeg
"""
import os
import subprocess
import tempfile
import hashlib
from pathlib import Path
from typing import List, Optional

from app.config import config


def extract_subtitle_ocr(
    video_path: str,
    fps: float = 2.0,
    crop_ratio: float = 0.35,
) -> List[dict]:
    """从视频中 OCR 提取硬字幕

    Args:
        video_path:  视频文件路径
        fps:         提帧频率（帧/秒），默认每秒2帧
        crop_ratio:  截取画面底部多少比例（字幕区域），默认 0.35

    Returns:
        字幕段列表 [{start, end, text}, ...]
    """
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    video_path = str(Path(video_path).resolve())

    # 获取视频时长和尺寸
    duration = _get_duration(video_path)
    if duration <= 0:
        return []

    # 获取视频高度，计算字幕区域
    height = _get_video_height(video_path)
    crop_h = int(height * crop_ratio)
    crop_y = height - crop_h

    print(f"[OCR字幕] 视频: {duration:.1f}s, {height}p, 字幕区: y={crop_y}-{height}")

    # ffmpeg 提帧到临时目录
    tmp_dir = tempfile.mkdtemp(prefix="ocr_frames_")
    frame_pattern = os.path.join(tmp_dir, "frame_%06d.jpg")

    # 每 1/fps 秒提取一帧，只截底部字幕区域
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"crop=iw:{crop_h}:0:{crop_y},fps={fps}",
        "-q:v", "2",
        frame_pattern,
        "-loglevel", "error",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"  ffmpeg 提帧失败: {r.stderr[:300]}")
        return []

    # 列出所有帧文件
    frames = sorted(Path(tmp_dir).glob("frame_*.jpg"))
    if not frames:
        print("  无帧文件")
        return []

    print(f"  提取 {len(frames)} 帧, 开始 OCR 识别...")

    # OCR 识别每帧
    frame_interval = 1.0 / fps
    raw_results = []  # [(time, text), ...]

    for i, frame_path in enumerate(frames):
        t = i * frame_interval
        result, _ = ocr(str(frame_path))

        if result:
            # 合并同一帧的所有文本块（从上到下排序）
            texts = [item[1] for item in result]
            combined = " ".join(texts).strip()
            if combined:
                raw_results.append((t, combined))

        # 每 50 帧打印一次进度
        if (i + 1) % 50 == 0:
            print(f"  OCR 进度: {i+1}/{len(frames)} ({t:.1f}s)")

    # 清理临时帧
    for f in frames:
        f.unlink(missing_ok=True)
    Path(tmp_dir).rmdir()

    if not raw_results:
        print("  OCR 未识别到任何文字")
        return []

    # 去重合并：相邻帧相同/相似文字合并为一条
    segments = _deduplicate(raw_results, frame_interval, duration)
    print(f"  OCR 完成: {len(raw_results)} 帧有文字 -> 合并为 {len(segments)} 段")

    return segments


def _deduplicate(
    raw: List[tuple],
    frame_interval: float,
    duration: float,
) -> List[dict]:
    """相邻帧相同/相似文字去重合并

    Args:
        raw:            [(time, text), ...]
        frame_interval: 帧间隔（秒）
        duration:       视频总时长

    Returns:
        [{start, end, text}, ...]
    """
    if not raw:
        return []

    segments = []
    cur_text = raw[0][1]
    cur_start = raw[0][0]
    cur_end = raw[0][0] + frame_interval

    for i in range(1, len(raw)):
        t, text = raw[i]
        if _text_similar(text, cur_text):
            # 相似，延长当前段
            cur_end = t + frame_interval
            # 取更长的文本（通常更完整）
            if len(text) > len(cur_text):
                cur_text = text
        else:
            # 不相似，保存当前段，开始新段
            segments.append({
                "start": round(cur_start, 2),
                "end": round(min(cur_end, duration), 2),
                "text": cur_text,
            })
            cur_text = text
            cur_start = t
            cur_end = t + frame_interval

    # 保存最后一段
    segments.append({
        "start": round(cur_start, 2),
        "end": round(min(cur_end, duration), 2),
        "text": cur_text,
    })

    return segments


def _text_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """判断两段文字是否相似（编辑距离比）"""
    if not a or not b:
        return False
    if a == b:
        return True
    # 快速判断：完全包含
    if a in b or b in a:
        return True
    # 编辑距离相似度
    dist = _levenshtein(a, b)
    max_len = max(len(a), len(b))
    similarity = 1 - dist / max_len
    return similarity >= threshold


def _levenshtein(a: str, b: str) -> int:
    """编辑距离"""
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _get_duration(video_path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _get_video_height(video_path: str) -> int:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=height", "-of", "default=noprint_wrappers=1:nokey=1",
           video_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        return int(r.stdout.strip())
    except (ValueError, AttributeError):
        return 1080
