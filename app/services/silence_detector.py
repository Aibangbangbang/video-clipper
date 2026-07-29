"""静音检测服务 - 用 ffmpeg silencedetect 检测无声片段，反推有声保留区间"""
import re
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

from app.config import config


@dataclass
class Range:
    start: float
    end: float

    def to_dict(self):
        return {"start": round(self.start, 3), "end": round(self.end, 3)}

    @property
    def duration(self):
        return self.end - self.start


def detect_silence(
    media_path: str,
    noise_db: float = None,
    min_duration: float = None,
) -> Tuple[List[Range], List[Range]]:
    """
    检测静音片段。

    Args:
        media_path: 音频/视频文件路径
        noise_db: 静音阈值(dB)，None则用config默认值
        min_duration: 最短静音时长(秒)，None则用config默认值

    Returns:
        (silence_ranges, keep_ranges)
        silence_ranges: 检测到的静音区间列表
        keep_ranges:    有声保留区间列表（静音被剔除后）
    """
    if noise_db is None:
        noise_db = config.silence.noise_db
    if min_duration is None:
        min_duration = config.silence.min_duration

    # 先获取总时长
    duration = _get_duration(media_path)

    # 运行 silencedetect
    cmd = [
        "ffmpeg", "-i", media_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    # silencedetect 输出在 stderr
    log = result.stderr

    silence_ranges = _parse_silence_log(log)

    # 反推有声区间
    keep_ranges = _silence_to_keep(silence_ranges, duration)

    print(f"[静音检测] 总时长={duration:.1f}s, 静音段={len(silence_ranges)}个, "
          f"保留段={len(keep_ranges)}个")
    for i, s in enumerate(silence_ranges):
        print(f"  静音{i+1}: {s.start:.1f}s - {s.end:.1f}s ({s.duration:.1f}s)")

    return silence_ranges, keep_ranges


def _parse_silence_log(log: str) -> List[Range]:
    """解析 ffmpeg silencedetect 的 stderr 输出"""
    ranges = []
    starts = re.findall(r"silence_start: ([\d.]+)", log)
    ends = re.findall(r"silence_end: ([\d.]+)", log)

    # 配对 start/end
    # silencedetect 可能出现 start 没有 end（结尾静音），此时 end = 视频结尾
    n = max(len(starts), len(ends))
    for i in range(n):
        s = float(starts[i]) if i < len(starts) else None
        e = float(ends[i]) if i < len(ends) else None
        if s is not None and e is not None:
            ranges.append(Range(s, e))
        elif s is not None:
            # 有 start 无 end，表示结尾静音，end 暂记为 None，后续用视频时长补
            ranges.append(Range(s, -1))  # -1 表示到结尾
        elif e is not None:
            # 有 end 无 start（理论不常见），start 记为 0
            ranges.append(Range(0, e))

    return ranges


def _silence_to_keep(silence_ranges: List[Range], duration: float) -> List[Range]:
    """从静音区间反推有声保留区间"""
    if not silence_ranges:
        return [Range(0, duration)] if duration > 0 else []

    keep = []
    prev_end = 0.0

    for sr in silence_ranges:
        s = sr.start
        e = duration if sr.end < 0 else sr.end  # -1 表示到结尾
        # prev_end ~ s 之间是有声段
        if s > prev_end:
            keep.append(Range(prev_end, s))
        prev_end = e

    # 最后一段静音之后的有声
    if prev_end < duration:
        keep.append(Range(prev_end, duration))

    return keep


def _get_duration(media_path: str) -> float:
    """用 ffprobe 获取时长"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def ranges_to_dict_list(ranges: List[Range]) -> List[dict]:
    return [r.to_dict() for r in ranges]


def complement_ranges(keep: List[Range], duration: float) -> List[Range]:
    """计算保留区间的补集（即被删除的区间）"""
    if not keep:
        return [Range(0, duration)] if duration > 0 else []
    removed = []
    prev_end = 0.0
    for r in sorted(keep, key=lambda x: x.start):
        if r.start > prev_end:
            removed.append(Range(prev_end, r.start))
        prev_end = max(prev_end, r.end)
    if prev_end < duration:
        removed.append(Range(prev_end, duration))
    return removed
