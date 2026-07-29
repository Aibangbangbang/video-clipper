"""静音检测服务 - 用 ffmpeg silencedetect 检测无声片段，反推有声保留区间"""
import re
import random
import subprocess
from dataclasses import dataclass
from typing import List, Tuple, Optional

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


def randomize_removed_ranges(
    removed: List[Range],
    min_ratio: float = 0.5,
    max_ratio: float = 1.0,
    seed: Optional[int] = None,
) -> List[Range]:
    """对每个待删除区间，随机选择一个子区间实际删除（反同质化）

    如某静音片段有 10 帧，可能只删前 2 帧或中间 5 帧，保留剩余部分。
    每次运行结果不同，使输出视频略有差异。

    Args:
        removed:    原始待删除区间列表
        min_ratio:  每段最少删除比例 (0.0-1.0)
        max_ratio:  每段最多删除比例 (0.0-1.0)，1.0=可整段删除
        seed:       随机种子，None=每次不同

    Returns:
        实际删除的子区间列表（每个子区间都在原区间内部）
    """
    rng = random.Random(seed) if seed is not None else random.Random()

    actual = []
    for r in removed:
        duration = r.end - r.start
        if duration <= 0:
            continue
        # 随机选择实际删除比例
        ratio = rng.uniform(min_ratio, max_ratio)
        delete_len = duration * ratio
        # 随机选择删除起始位置
        max_offset = duration - delete_len
        if max_offset <= 0:
            actual.append(Range(r.start, r.end))
        else:
            offset = rng.uniform(0, max_offset)
            actual.append(Range(r.start + offset, r.start + offset + delete_len))

    return actual


def scatter_delete_range(
    r: Range,
    fps: float = 30.0,
    seed: Optional[int] = None,
) -> List[Range]:
    """散粒删除：在区间内每隔随机帧数删除 1-2 帧

    .. deprecated:: 此函数会产生大量微片段拼接导致跳帧，不再用于句中 gap。
       保留仅供测试参考。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    frame_dur = 1.0 / fps
    duration = r.end - r.start
    if duration <= frame_dur * 3:
        return []

    deleted = []
    pos = r.start + rng.uniform(0, frame_dur * 2)
    while pos < r.end - frame_dur:
        n_frames = rng.randint(1, 2)
        del_end = min(pos + n_frames * frame_dur, r.end)
        deleted.append(Range(pos, del_end))
        gap_frames = rng.randint(3, 15)
        pos = del_end + gap_frames * frame_dur

    return deleted


def merge_close_ranges(ranges: List[Range], min_gap: float = 0.3) -> List[Range]:
    """合并间隔小于 min_gap 的相邻区间，避免产生跳帧

    如果两个保留区间之间只隔 0.1s 的删除区间，删除后拼接会造成画面跳变。
    合并后这段删除区间被保留，画面连续。

    Args:
        ranges:   已排序的区间列表
        min_gap:  最小间隔阈值（秒），间隔小于此值的相邻区间合并

    Returns:
        合并后的区间列表
    """
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda r: r.start)
    merged = [Range(ranges[0].start, ranges[0].end)]
    for r in ranges[1:]:
        if r.start - merged[-1].end < min_gap:
            # 间隔太小，合并（保留中间的 gap）
            merged[-1].end = max(merged[-1].end, r.end)
        else:
            merged.append(Range(r.start, r.end))
    return merged


def classify_and_randomize(
    removed: List[Range],
    segments: List[dict],
    duration: float,
    min_ratio: float = 0.5,
    max_ratio: float = 1.0,
    fps: float = 30.0,
    sentence_gap_threshold: float = 1.5,
    seed: Optional[int] = None,
) -> List[Range]:
    """智能随机删除：区分句间 gap 和句中 gap，采用不同策略

    - 句间 gap（前后属于不同句子/长时间停顿）：大面积随机删除
    - 句中 gap（前后属于同一句完整话语）：散粒删除 1-2 帧，保持连贯

    判定逻辑：如果一个 removed 区间的前后都有 transcript segment，
    且前后 segment 的时间差（即该 gap 的时长）小于 sentence_gap_threshold，
    则认为是句中 gap。

    Args:
        removed:                  原始待删除区间列表
        segments:                 转写片段 [{start, end, text}, ...]
        duration:                 视频总时长
        min_ratio / max_ratio:    句间 gap 的随机删除比例范围
        fps:                      视频帧率
        sentence_gap_threshold:   句中 gap 判定阈值（秒），小于此值认为是句中
        seed:                     随机种子

    Returns:
        实际删除的区间列表
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    if not removed:
        return []

    # 构建 segment 时间端点列表，用于判断 removed 区间前后是否有文字
    seg_starts = sorted(s["start"] for s in segments) if segments else []
    seg_ends = sorted(s["end"] for s in segments) if segments else []

    import bisect

    actual = []
    for r in removed:
        dur = r.end - r.start
        if dur <= 0:
            continue

        # 查找 r.start 之前最近的 segment end（即 gap 前面有没有文字）
        idx_before = bisect.bisect_right(seg_ends, r.start) - 1
        has_text_before = idx_before >= 0 and (r.start - seg_ends[idx_before]) < 0.5

        # 查找 r.end 之后最近的 segment start（即 gap 后面有没有文字）
        idx_after = bisect.bisect_left(seg_starts, r.end)
        has_text_after = idx_after < len(seg_starts) and (seg_starts[idx_after] - r.end) < 0.5

        is_mid_sentence = (
            has_text_before and has_text_after and dur < sentence_gap_threshold
        )

        if is_mid_sentence:
            # 句中 gap：完全不删，保留画面连续（散粒删除会导致跳帧）
            print(f"  [句中gap] {r.start:.2f}-{r.end:.2f} ({dur:.2f}s) -> 保留(不删除)")
        else:
            # 句间 gap：大面积随机删除
            ratio = rng.uniform(min_ratio, max_ratio)
            delete_len = dur * ratio
            max_offset = dur - delete_len
            if max_offset <= 0:
                actual.append(Range(r.start, r.end))
            else:
                offset = rng.uniform(0, max_offset)
                actual.append(Range(r.start + offset, r.start + offset + delete_len))
            print(f"  [句间gap] {r.start:.2f}-{r.end:.2f} ({dur:.2f}s) "
                  f"-> 删除 {delete_len:.2f}s")

    return actual
