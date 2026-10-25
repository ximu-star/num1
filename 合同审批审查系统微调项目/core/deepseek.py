"""DeepSeek API 客户端：造数据与 LLM Judge 共用。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests
from dotenv import load_dotenv

import config as C


class DeepSeekError(RuntimeError):
    pass


def load_api_key() -> str:
    """从项目根目录 .env 或当前环境读取 Key，绝不打印 Key 本身。"""
    load_dotenv(C.ENV_FILE, override=False)
    api_key = os.environ.get(C.DEEPSEEK_API_KEY_ENV, "").strip()
    if not api_key:
        raise DeepSeekError(
            f"未找到 {C.DEEPSEEK_API_KEY_ENV}。请复制 .env.example 为 .env 并填写。"
        )
    return api_key


class DeepSeekClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or load_api_key()

    def chat_json(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 chat/completions，要求返回 JSON 对象，失败自动重试。"""
        payload = {
            "model": model or C.DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": C.DEEPSEEK_TEMPERATURE if temperature is None else temperature,
            "max_tokens": C.DEEPSEEK_MAX_TOKENS if max_tokens is None else max_tokens,
            "response_format": {"type": "json_object"},
        }
        last_error: Exception | None = None
        for attempt in range(1, C.DEEPSEEK_MAX_RETRIES + 1):
            try:
                response = requests.post(
                    C.DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=C.DEEPSEEK_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                choice = response.json()["choices"][0]
                content = choice["message"].get("content", "")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"空响应，finish_reason={choice.get('finish_reason')}")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("模型返回的 JSON 顶层不是对象")
                return parsed
            except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < C.DEEPSEEK_MAX_RETRIES:
                    time.sleep(C.DEEPSEEK_RETRY_SLEEP_SECONDS * attempt)
        raise DeepSeekError(f"DeepSeek 连续 {C.DEEPSEEK_MAX_RETRIES} 次失败: {last_error}")


def get_client() -> DeepSeekClient:
    return DeepSeekClient()
