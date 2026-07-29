"""字幕融合服务 - OCR 字幕 + ASR 语音转写 融合

融合策略：
  1. 按时间戳对齐 OCR 段和 ASR 段
  2. 同一时段 OCR 有文字 -> 用 OCR（人工编辑的字幕更准确）
  3. 同一时段只有 ASR -> 用 ASR（补充无字幕的语音）
  4. OCR 文字修正 ASR 的错别字

输入：
  ocr_segments: [{start, end, text}, ...]  (OCR 提取)
  asr_segments: [{start, end, text}, ...]  (Whisper 转写)

输出：
  fused_segments: [{start, end, text, source}, ...]
    source: "ocr" | "asr" | "fused"
"""
from typing import List


def fuse_subtitles(
    ocr_segments: List[dict],
    asr_segments: List[dict],
) -> List[dict]:
    """融合 OCR 字幕和 ASR 语音转写

    Args:
        ocr_segments: OCR 提取的字幕段
        asr_segments: ASR 转写的语音段

    Returns:
        融合后的字幕段 [{start, end, text, source}, ...]
    """
    if not ocr_segments and not asr_segments:
        return []
    if not ocr_segments:
        return [{**s, "source": "asr"} for s in asr_segments]
    if not asr_segments:
        return [{**s, "source": "ocr"} for s in ocr_segments]

    # 按时间线合并
    # 策略：以 ASR 段为基础（分段更准确），用 OCR 文字修正
    fused = []
    ocr_idx = 0

    for asr_seg in asr_segments:
        a_start = asr_seg["start"]
        a_end = asr_seg["end"]
        a_text = asr_seg["text"].strip()

        # 找到与此 ASR 段时间重叠的 OCR 段
        overlapping_ocr = []
        while ocr_idx < len(ocr_segments):
            ocr_seg = ocr_segments[ocr_idx]
            o_start = ocr_seg["start"]
            o_end = ocr_seg["end"]

            # OCR 段在 ASR 段之前，跳过
            if o_end < a_start:
                # 但如果这个 OCR 段没有被任何 ASR 段覆盖，需要补入
                if not fused or fused[-1]["end"] < o_start:
                    fused.append({
                        "start": o_start, "end": o_end,
                        "text": ocr_seg["text"], "source": "ocr",
                    })
                ocr_idx += 1
                continue

            # OCR 段在 ASR 段之后，停止
            if o_start > a_end:
                break

            # 有重叠
            overlapping_ocr.append(ocr_seg)
            ocr_idx += 1

        if overlapping_ocr:
            # 有 OCR 文字，用 OCR 修正 ASR
            ocr_text = " ".join(s["text"].strip() for s in overlapping_ocr)
            # 如果 OCR 和 ASR 差异不大，用 OCR（更准确）
            # 如果差异很大，可能是不同内容，保留两者
            similarity = _text_similarity(a_text, ocr_text)
            if similarity > 0.3:
                # 相似度高：用 OCR 文字修正 ASR
                fused.append({
                    "start": a_start, "end": a_end,
                    "text": ocr_text, "source": "fused",
                })
            else:
                # 差异大：OCR 可能是画面的其他文字，保留 ASR
                fused.append({
                    "start": a_start, "end": a_end,
                    "text": a_text, "source": "asr",
                })
        else:
            # 无 OCR 文字，用 ASR
            fused.append({
                "start": a_start, "end": a_end,
                "text": a_text, "source": "asr",
            })

    # 补入剩余的 OCR 段（在最后一个 ASR 段之后的）
    while ocr_idx < len(ocr_segments):
        ocr_seg = ocr_segments[ocr_idx]
        if not fused or fused[-1]["end"] < ocr_seg["start"]:
            fused.append({
                "start": ocr_seg["start"], "end": ocr_seg["end"],
                "text": ocr_seg["text"], "source": "ocr",
            })
        ocr_idx += 1

    # 按时间排序
    fused.sort(key=lambda s: s["start"])

    # 统计
    ocr_count = sum(1 for s in fused if s["source"] == "ocr")
    asr_count = sum(1 for s in fused if s["source"] == "asr")
    fused_count = sum(1 for s in fused if s["source"] == "fused")
    print(f"[字幕融合] OCR段={len(ocr_segments)}, ASR段={len(asr_segments)} "
          f"-> 融合后={len(fused)}段 "
          f"(OCR修正={fused_count}, 纯OCR={ocr_count}, 纯ASR={asr_count})")

    return fused


def _text_similarity(a: str, b: str) -> float:
    """计算两段文字的相似度（基于字符集合和编辑距离）"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 字符集合相似度（Jaccard）
    set_a = set(a)
    set_b = set(b)
    jaccard = len(set_a & set_b) / len(set_a | set_b) if (set_a | set_b) else 0

    # 编辑距离相似度
    dist = _levenshtein(a, b)
    max_len = max(len(a), len(b))
    edit_sim = 1 - dist / max_len if max_len > 0 else 0

    # 取较大值（任一指标高就认为可能相似）
    return max(jaccard, edit_sim)


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
