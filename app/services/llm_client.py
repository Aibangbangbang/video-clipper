"""LLM 客户端 - 从 Hermes 配置自动加载可用 provider

支持两种协议：
  - chat_completions (OpenAI 兼容): 火山方舟 DeepSeek / apikey.fun
  - anthropic_messages: 讯飞星火

自动从 ~/.hermes/config.yaml 的 custom_providers 中找到可用的 provider。
"""
import os
import json
import re
import httpx
import logging
from typing import Optional, List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)


class LLMClient:
    """通用 LLM 客户端"""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "",
                 api_mode: str = ""):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_mode = api_mode

        # 没配置就从 Hermes 配置加载
        if not self.api_key:
            self._load_from_hermes()

        self._client: Optional[httpx.AsyncClient] = None

    def _load_from_hermes(self):
        """从 ~/.hermes/config.yaml 的 custom_providers 加载第一个有 key 的 provider"""
        try:
            import yaml
            cfg_path = str(Path.home() / ".hermes" / "config.yaml")
            if not os.path.exists(cfg_path):
                logger.warning("Hermes 配置不存在")
                return
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            providers = cfg.get("custom_providers", [])
            # 优先选择非讯飞的（讯飞 key 可能过期），火山方舟/apikey.fun 优先
            preferred = ["ark.cn-beijing", "apikey.fun", "xf-yun"]
            for pattern in preferred:
                for p in providers:
                    if p.get("api_key") and pattern in p.get("base_url", ""):
                        self._apply_provider(p)
                        logger.info(f"LLM 使用 provider: {p.get('name')} @ {self.base_url}")
                        return
            # 兜底：第一个有 key 的
            for p in providers:
                if p.get("api_key"):
                    self._apply_provider(p)
                    logger.info(f"LLM 使用 provider: {p.get('name')} @ {self.base_url}")
                    return
        except Exception as e:
            logger.warning(f"加载 Hermes LLM 配置失败: {e}")

    def _apply_provider(self, p: dict):
        self.api_key = p.get("api_key", "")
        self.base_url = p.get("base_url", "").rstrip("/")
        self.model = p.get("model", "")
        self.api_mode = p.get("api_mode") or "chat_completions"
        # 讯飞 base_url 是 /anthropic/v1，模型用 anthropic_messages
        if "anthropic" in self.base_url or self.api_mode == "anthropic_messages":
            self.api_mode = "anthropic_messages"

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def chat(self, messages: List[Dict[str, str]],
                   temperature: float = 0.3, max_tokens: int = 4096) -> str:
        """发送聊天请求，返回文本"""
        if not self.api_key:
            raise RuntimeError("LLM 未配置：请在 ~/.hermes/config.yaml 配置 custom_providers")

        if self.api_mode == "anthropic_messages":
            return await self._chat_anthropic(messages, temperature, max_tokens)
        else:
            return await self._chat_openai(messages, temperature, max_tokens)

    async def _chat_openai(self, messages: List[Dict[str, str]],
                           temperature: float, max_tokens: int) -> str:
        """OpenAI Chat Completions 协议（火山方舟/apikey.fun）"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(3):
            try:
                resp = await self.client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                body = e.response.text[:300]
                logger.warning(f"LLM 请求失败 (attempt {attempt+1}): {e.response.status_code} {body}")
                if attempt == 2:
                    raise RuntimeError(f"LLM 请求失败 ({e.response.status_code}): {body}")
            except Exception as e:
                logger.warning(f"LLM 请求异常 (attempt {attempt+1}): {e}")
                if attempt == 2:
                    raise
        raise RuntimeError("LLM 请求失败：重试耗尽")

    async def _chat_anthropic(self, messages: List[Dict[str, str]],
                              temperature: float, max_tokens: int) -> str:
        """Anthropic Messages 协议（讯飞星火）"""
        # 分离 system 消息
        system_content = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content += msg.get("content", "") + "\n"
            else:
                chat_messages.append(msg)

        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content.strip():
            payload["system"] = system_content.strip()

        for attempt in range(3):
            try:
                resp = await self.client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if "content" in data and data["content"]:
                    return data["content"][0].get("text", "")
                return json.dumps(data, ensure_ascii=False)
            except httpx.HTTPStatusError as e:
                body = e.response.text[:300]
                logger.warning(f"LLM 请求失败 (attempt {attempt+1}): {e.response.status_code} {body}")
                if attempt == 2:
                    raise RuntimeError(f"LLM 请求失败 ({e.response.status_code}): {body}")
            except Exception as e:
                logger.warning(f"LLM 请求异常 (attempt {attempt+1}): {e}")
                if attempt == 2:
                    raise
        raise RuntimeError("LLM 请求失败：重试耗尽")

    async def chat_json(self, messages: List[Dict[str, str]],
                        temperature: float = 0.3) -> dict:
        """发送请求并解析 JSON 结果（容错处理）"""
        text = await self.chat(messages, temperature)
        return _parse_json_robust(text)


def _parse_json_robust(text: str) -> dict:
    """健壮 JSON 解析：处理 markdown 包裹、尾逗号、多余文本"""
    # 去掉 markdown 代码块
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    text = text.strip()

    # 可能是 JSON 数组
    if text.startswith("["):
        end = text.rfind("]")
        if end != -1:
            text = text[:end + 1]
        text = re.sub(r",\s*]", "]", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    # 去尾逗号
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text.replace("'", '"'))
        except Exception:
            logger.error(f"JSON 解析失败: {text[:300]}")
            return {"error": "json_parse_failed", "raw": text[:500]}
