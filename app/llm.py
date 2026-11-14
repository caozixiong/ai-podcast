"""撰稿 LLM 客户端。

Provider：Google AI Studio（Gemini），走其 OpenAI 兼容端点。
API key 从环境变量读取（默认 GEMINI_API_KEY），**绝不打印、绝不写入文件**。
失败有限重试 + 指数退避；可配置备用模型。
"""
from __future__ import annotations

import os
import time

import requests

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-2.5-flash"


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None, temperature=0.6,
                 max_tokens=4000, timeout_s=120, max_retries=2, fallback_model=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def _headers(self) -> dict:
        if not self.api_key:
            raise LLMError("缺少 API key：请在环境变量设置 GEMINI_API_KEY（Google AI Studio）。")
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def _one(self, system: str, user: str, model: str, temperature, max_tokens) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
        raise LLMError(f"模型 {model} 调用失败（重试 {self.max_retries} 次）：{last_err}")

    def complete(self, system: str, user: str, *, temperature=None, max_tokens=None) -> str:
        try:
            return self._one(system, user, self.model, temperature, max_tokens)
        except LLMError:
            if not self.fallback_model:
                raise
        return self._one(system, user, self.fallback_model, temperature, max_tokens)
