"""撰稿 LLM 客户端。

Provider：Google AI Studio（Gemini），走其 OpenAI 兼容端点。
API key 从环境变量读取（默认 GEMINI_API_KEY），**绝不打印、绝不写入文件**。
失败有限重试 + 指数退避；可配置备用模型。
"""
from __future__ import annotations

import os
import time

import requests

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None, temperature=0.6,
                 max_tokens=4000, timeout_s=120, max_retries=2, fallback_model=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/openai"):
            base = base[: -len("/openai")]
        self.base_url = base
        self.model = model or DEFAULT_MODEL
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def _one(self, system, user, model, temperature, max_tokens, json_mode=False) -> str:
        if "generativelanguage.googleapis.com" in self.base_url:
            return self._gemini(system, user, model, temperature, max_tokens, json_mode)
        if self.base_url.rstrip("/").endswith("/v1"):
            return self._openai(system, user, model, temperature, max_tokens, json_mode)
        return self._ollama_native(system, user, model, temperature, max_tokens, json_mode)

    def _openai(self, system, user, model, temperature, max_tokens, json_mode=False) -> str:
        """OpenAI 兼容端点：Ollama / LM Studio / 其它。"""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": model,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=self.timeout_s)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
            except LLMError:
                raise
        raise LLMError(f"模型 {model}（OpenAI 兼容）调用失败：{last_err}")

    def _ollama_native(self, system, user, model, temperature, max_tokens, json_mode=False) -> str:
        """Ollama 原生 /api/chat：对思考型模型加 think:false 拿干净 content。"""
        url = f"{self.base_url.rstrip('/')}/api/chat"
        body = {
            "model": model, "think": False, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": self.max_tokens if max_tokens is None else max_tokens,
            },
        }
        if json_mode:
            body["format"] = "json"
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, json=body, timeout=self.timeout_s)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                msg = r.json().get("message", {})
                return msg.get("content") or msg.get("reasoning") or ""
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
            except LLMError:
                raise
        raise LLMError(f"Ollama {model} 调用失败：{last_err}")

    def _gemini(self, system, user, model, temperature, max_tokens, json_mode=False) -> str:
        """Google AI Studio 原生 generateContent。"""
        if not self.api_key:
            raise LLMError("调用 Gemini 需要 GEMINI_API_KEY。")
        url = f"{self.base_url}/models/{model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.temperature if temperature is None else temperature,
                "maxOutputTokens": self.max_tokens if max_tokens is None else max_tokens,
            },
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=self.timeout_s)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
            except (KeyError, IndexError, TypeError):
                last_err = "Gemini 返回无正文（可能被安全过滤/额度）"
                time.sleep(1)
            except LLMError:
                raise
        raise LLMError(f"Gemini {model} 调用失败：{last_err}")

    def complete(self, system: str, user: str, *, temperature=None, max_tokens=None,
                 json_mode: bool = False) -> str:
        """json_mode=True 时要求模型只输出一个 JSON 对象（Ollama format=json 等）。"""
        try:
            return self._one(system, user, self.model, temperature, max_tokens, json_mode)
        except LLMError:
            if not self.fallback_model:
                raise
        return self._one(system, user, self.fallback_model, temperature, max_tokens, json_mode)
