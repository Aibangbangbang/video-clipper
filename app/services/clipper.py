"""统一剪辑引擎 - 给定保留区间列表，用 ffmpeg trim+concat 一次性精确剪辑

三个需求统一为：
  1. 静音删除 → keep_ranges = 有声区间
  2. 关键词删除 → keep_ranges = 不含关键词的片段
  3. 组合 → 两个 keep_ranges 取交集

本模块只负责「按 keep_ranges 切片并合并」，不关心 keep_ranges 怎么算出来的。
使用 filter_complex 的 trim/atrim + setpts + concat filter，
一次 ffmpeg 调用完成精确切割与合并（帧级精确，时间戳正确）。
"""
import os
import uuid
import subprocess
from pathlib import Path
from typing import List

from app.config import config
from app.services.silence_detector import Range, merge_close_ranges


class Clipper:
    def __init__(self):
        self._output_dir = None
        self._codec = None
        self._crf = None

    def _ensure(self):
        if self._output_dir is None:
            base = Path(__file__).parent.parent.parent
            self._output_dir = (base / config.paths.output).resolve()
            self._output_dir.mkdir(parents=True, exist_ok=True)
            self._codec = config.ffmpeg.codec
            self._crf = config.ffmpeg.crf

    @property
    def output_dir(self):
        self._ensure()
        return self._output_dir

    def get_video_duration(self, media_path: str) -> float:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        try:
            return float(r.stdout.strip())
        except (ValueError, AttributeError):
            return 0.0

    def has_audio(self, media_path: str) -> bool:
        """检查媒体文件是否包含音频流"""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return "audio" in r.stdout

    def extract_audio(self, video_path: str, audio_path: str):
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", audio_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"音频提取失败: {r.stderr[:500]}")

    def _run(self, cmd: list, timeout: int = 600):
        print(f"  ffmpeg 命令长度: {len(' '.join(cmd))} 字符")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            print(f"  ffmpeg stderr (末尾800字): {r.stderr[-800:]}")
        return r

    def clip_by_ranges(self, input_path: str, keep_ranges: List[Range], output_name: str) -> str:
        """
        按保留区间切片并合并为一个视频。
        使用 filter_complex trim+concat，帧级精确，一次完成。
        每个音频片段首尾加微淡入淡出，消除拼接点"咻"声。

        Args:
            input_path: 源视频路径
            keep_ranges: 保留区间列表
            output_name: 输出文件名（不含扩展名）

        Returns:
            输出文件路径
        """
        if not keep_ranges:
            raise ValueError("保留区间为空，无法剪辑")

        # 合并间隔 < 0.3s 的相邻保留区间，避免跳帧
        # 如果两个保留片段之间只隔 0.1s 的删除，拼接后画面会跳变
        keep_ranges = merge_close_ranges(keep_ranges, min_gap=0.3)
        print(f"  合并相邻区间(间隔<0.3s)后: {len(keep_ranges)} 段")

        input_path = Path(input_path).resolve()
        out_path = self.output_dir / f"{output_name}.mp4"
        has_audio = self.has_audio(str(input_path))

        # 音频淡入淡出时长（秒），消除拼接点波形突变
        FADE = 0.01  # 10ms

        n = len(keep_ranges)
        filters = []
        concat_inputs = []

        for i, r in enumerate(keep_ranges):
            s = max(0.0, r.start)
            e = r.end
            dur = e - s
            print(f"  片段 {i+1}/{n}: {s:.2f}s-{e:.2f}s ({dur:.2f}s)")

            if has_audio:
                filters.append(
                    f"[0:v]trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS[v{i}]"
                )
                # 音频：atrim + asetpts + 首尾淡入淡出
                # 对极短片段，淡入淡出各取时长的 1/3，避免重叠
                fade = min(FADE, dur / 3) if dur > 0.003 else 0.0
                fade_out_st = max(0.0, dur - fade)
                a_chain = f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS"
                if fade > 0.001:
                    a_chain += f",afade=t=in:st=0:d={fade:.4f},afade=t=out:st={fade_out_st:.4f}:d={fade:.4f}"
                a_chain += f"[a{i}]"
                filters.append(a_chain)
                concat_inputs.append(f"[v{i}][a{i}]")
            else:
                filters.append(
                    f"[0:v]trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS[v{i}]"
                )
                concat_inputs.append(f"[v{i}]")

        if has_audio:
            concat = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[vout][aout]"
        else:
            concat = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[vout]"

        filter_complex = ";".join(filters + [concat])

        cmd = ["ffmpeg", "-y", "-i", str(input_path),
               "-filter_complex", filter_complex]

        if has_audio:
            cmd += ["-map", "[vout]", "-map", "[aout]"]
        else:
            cmd += ["-map", "[vout]"]

        cmd += [
            "-c:v", self._codec, "-crf", str(self._crf),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ]

        print(f"  合并 {n} 个片段 -> {out_path.name} (音频淡入淡出 {FADE*1000:.0f}ms)")
        r = self._run(cmd, timeout=600)

        if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 1000:
            raise RuntimeError(f"剪辑失败: {r.stderr[-500:]}")

        size_mb = out_path.stat().st_size / 1024 / 1024
        # 验证输出时长
        out_dur = self.get_video_duration(str(out_path))
        total_keep = sum(r.duration for r in keep_ranges)
        print(f"  完成: {out_path} ({size_mb:.1f}MB, {out_dur:.1f}s)"
              f" | 预期保留 {total_keep:.1f}s")
        return str(out_path)


def intersect_ranges(a: List[Range], b: List[Range]) -> List[Range]:
    """计算两组区间的交集（用于静音删除+关键词删除组合）"""
    if not a or not b:
        return []
    a = sorted(a, key=lambda r: r.start)
    b = sorted(b, key=lambda r: r.start)
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i].start, b[j].start)
        hi = min(a[i].end, b[j].end)
        if lo < hi:
            result.append(Range(lo, hi))
        if a[i].end < b[j].end:
            i += 1
        else:
            j += 1
    return result


clipper = Clipper()
