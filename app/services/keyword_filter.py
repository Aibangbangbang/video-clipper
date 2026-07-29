"""关键词过滤服务 - 根据黑名单关键词，从转写片段中计算保留/删除区间

核心逻辑：
  遍历 transcript segments，命中关键词的整段标记为「删除」，
  其余段标记为「保留」。
  同时支持将命中段前后各扩展 margin 秒（避免截断句子）。
"""
from typing import List
from app.services.silence_detector import Range, ranges_to_dict_list


def filter_by_keywords(
    segments: List[dict],
    keywords: List[str],
    margin: float = 0.3,
) -> tuple:
    """
    根据关键词黑名单过滤片段。

    Args:
        segments: 转写片段 [{start, end, text}, ...]
        keywords: 黑名单关键词列表（命中任一即删除该段）
        margin:   命中段前后扩展秒数，避免截断

    Returns:
        (keep_ranges, removed_ranges)
        keep_ranges:    不含关键词的保留区间
        removed_ranges: 命中关键词被删除的区间
    """
    if not segments:
        return [], []

    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        # 无关键词，全部保留
        keep = [Range(s["start"], s["end"]) for s in segments]
        return keep, []

    keep_ranges = []
    removed_ranges = []
    removed_details = []  # 调试用

    for seg in segments:
        text = seg.get("text", "")
        start = seg["start"]
        end = seg["end"]
        hit = any(kw in text for kw in keywords)

        if hit:
            # 命中：扩展 margin 后标记为删除
            r_start = max(0, start - margin)
            r_end = end + margin
            removed_ranges.append(Range(r_start, r_end))
            removed_details.append({"start": start, "end": end, "text": text})
        else:
            # 未命中：保留
            keep_ranges.append(Range(start, end))

    print(f"[关键词过滤] 关键词={keywords}, 片段数={len(segments)}, "
          f"删除={len(removed_ranges)}段, 保留={len(keep_ranges)}段")
    for d in removed_details:
        print(f"  删除: [{d['start']:.1f}-{d['end']:.1f}s] {d['text'][:40]}")

    return keep_ranges, removed_ranges


def merge_ranges(ranges: List[Range]) -> List[Range]:
    """合并重叠/相邻的区间"""
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda r: r.start)
    merged = [ranges[0]]
    for r in ranges[1:]:
        if r.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, r.end)
        else:
            merged.append(r)
    return merged


def segments_to_keep_ranges(segments: List[dict], margin: float = 0.0) -> List[Range]:
    """把转写 segments 转为有文字的保留区间（用于删除无文字部分）

    Args:
        segments: 转写片段 [{start, end, text}, ...]
        margin:   每段前后扩展秒数，避免截断语音

    Returns:
        合并后的有文字保留区间列表
    """
    if not segments:
        return []
    ranges = []
    for s in segments:
        start = max(0.0, s["start"] - margin)
        end = s["end"] + margin
        ranges.append(Range(start, end))
    return merge_ranges(ranges)
