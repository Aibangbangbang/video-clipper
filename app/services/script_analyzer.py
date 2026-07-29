"""文案结构分析器 - LLM 把语音段落分类为文案角色

角色体系（短视频/抖音文案）：
  hook       开头钩子（吸引注意、制造悬念）
  pain_point 痛点描述（用户的问题/困扰）
  product    产品/方案介绍
  evidence   案例/证据/数据
  summary    内容总结
  cta        行动号召（关注/点赞/购买引导）
  filler     废话填充（语气词、无意义重复、口头禅）
  transition 过渡衔接
  repeat     重复内容（和前面说过的重复）
  content    正文核心内容（不属于以上分类的有价值内容）

学习流程：分析参考视频 -> 保存角色模板
应用流程：分析新视频 -> 按模板删除指定角色
"""
import json
import logging
from typing import List, Dict, Any, Optional

from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# 角色中文说明（给 LLM 看的）
ROLE_DESC = {
    "hook": "开头钩子：吸引注意力、制造悬念、抛出问题",
    "pain_point": "痛点描述：用户的问题、困扰、需求",
    "product": "产品/方案介绍：介绍解决方案、产品功能",
    "evidence": "案例/证据：数据、案例、用户反馈、对比",
    "summary": "内容总结：归纳要点、结论",
    "cta": "行动号召：引导关注、点赞、购买、收藏",
    "filler": "废话填充：语气词、口头禅、无意义重复、凑时长",
    "transition": "过渡衔接：承上启下、话题转换",
    "repeat": "重复内容：和前面说过的内容重复",
    "content": "正文核心：有价值的实质内容",
}

# 默认删除的角色（废话类）
DEFAULT_DELETE_ROLES = ["filler", "repeat"]


class ScriptAnalyzer:
    """文案结构分析器"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    async def analyze_segments(self, segments: List[Dict]) -> List[Dict]:
        """分析语音段落，给每段打上角色标签

        Args:
            segments: [{start, end, text}, ...]

        Returns:
            [{start, end, text, role, role_desc, reason}, ...]
        """
        if not segments:
            return []

        # 构造段落列表给 LLM
        seg_list = []
        for i, s in enumerate(segments):
            seg_list.append({
                "idx": i,
                "start": round(s.get("start", 0), 2),
                "end": round(s.get("end", 0), 2),
                "text": s.get("text", "").strip(),
            })

        # 分批处理（避免单次过长）
        BATCH = 30
        results = []
        for i in range(0, len(seg_list), BATCH):
            batch = seg_list[i:i + BATCH]
            batch_result = await self._analyze_batch(batch)
            results.extend(batch_result)

        # 合并回原始段落（保留时间信息）
        analyzed = []
        for i, s in enumerate(segments):
            r = results[i] if i < len(results) else {}
            analyzed.append({
                "start": s.get("start", 0),
                "end": s.get("end", 0),
                "text": s.get("text", ""),
                "role": r.get("role", "content"),
                "reason": r.get("reason", ""),
            })
        return analyzed

    async def _analyze_batch(self, batch: List[Dict]) -> List[Dict]:
        """分析一批段落"""
        role_desc_str = "\n".join(f"  - {k}: {v}" for k, v in ROLE_DESC.items())

        system_msg = f"""你是短视频文案结构分析专家。将给定的语音段落分类为以下角色之一：

{role_desc_str}

分类原则：
1. 根据段落内容和上下文判断其在文案中的功能
2. "filler" 只用于纯废话：语气词、口头禅、无意义重复、凑时长的话
3. "repeat" 只用于和前面内容实质性重复的段落
4. 有实质内容的段落归为对应功能角色或 "content"
5. 判断要果断，每段只选一个角色"""

        user_msg = f"""请分析以下{len(batch)}个语音段落，为每段分配角色。

段落列表（JSON）：
{json.dumps(batch, ensure_ascii=False, indent=2)}

请返回 JSON 数组，每个元素包含：
- idx: 段落序号
- role: 角色标签（从上面列表选一个）
- reason: 简短理由（10字以内）

只返回 JSON 数组，不要其他文字。格式：
[{{"idx": 0, "role": "hook", "reason": "开头吸引注意"}}, ...]"""

        messages = [
            {"role": "user", "content": system_msg + "\n\n" + user_msg}
        ]

        try:
            data = await self.llm.chat_json(messages, temperature=0.2)
            # 提取结果
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            logger.warning(f"LLM 返回格式异常: {data}")
            return [{"idx": s["idx"], "role": "content", "reason": ""} for s in batch]
        except Exception as e:
            logger.error(f"文案分析失败: {e}")
            return [{"idx": s["idx"], "role": "content", "reason": ""} for s in batch]

    async def generate_template_config(self, analyzed: List[Dict]) -> Dict:
        """根据分析结果生成模板配置建议

        分析参考视频后，建议哪些角色保留、哪些删除。
        默认删除 filler 和 repeat，其余保留。
        用户可以手动调整。

        Returns:
            {
                "role_stats": {role: {count, duration, ratio, examples}},
                "delete_roles": ["filler", "repeat"],
                "keep_roles": ["hook", "content", ...],
            }
        """
        # 统计每个角色
        role_stats: Dict[str, Dict] = {}
        total_dur = 0
        for s in analyzed:
            role = s.get("role", "content")
            dur = s.get("end", 0) - s.get("start", 0)
            total_dur += dur
            if role not in role_stats:
                role_stats[role] = {"count": 0, "duration": 0, "examples": []}
            role_stats[role]["count"] += 1
            role_stats[role]["duration"] += dur
            if len(role_stats[role]["examples"]) < 3:
                role_stats[role]["examples"].append(s.get("text", "")[:30])

        # 计算比例
        for role, st in role_stats.items():
            st["ratio"] = round(st["duration"] / total_dur, 3) if total_dur > 0 else 0
            st["duration"] = round(st["duration"], 2)

        delete_roles = [r for r in DEFAULT_DELETE_ROLES if r in role_stats]
        keep_roles = [r for r in role_stats if r not in delete_roles]

        return {
            "role_stats": role_stats,
            "delete_roles": delete_roles,
            "keep_roles": keep_roles,
            "total_duration": round(total_dur, 2),
            "segment_count": len(analyzed),
        }
