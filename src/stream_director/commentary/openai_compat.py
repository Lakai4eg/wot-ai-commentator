"""Бэкенд комментариев: любой OpenAI-совместимый API (chat/completions).

Одним модулем закрываются Groq, OpenRouter, Mistral, GitHub Models,
DeepSeek, локальный Ollama и OpenAI-endpoint Gemini — меняются только
base_url, ключ и имя модели.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class OpenAICompatBackend:
    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_s: float = 4.0,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.last_error: str | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        # Ключ опционален: локальному Ollama он не нужен.
        return bool(self.base_url.strip() and self.model.strip())

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def generate(self, prompt: str) -> str | None:
        if not self.configured:
            self.last_error = "base_url или модель не заданы"
            return None
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80,
        }
        try:
            resp = await self._get_client().post(
                url, json=payload, headers=headers, timeout=self.timeout_s
            )
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                log.warning("OpenAI-compat error %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            text = str(data["choices"][0]["message"]["content"] or "").strip()
            if not text:
                self.last_error = "пустой ответ"
                return None
            self.last_error = None
            return text
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("OpenAI-compat request failed: %s", self.last_error)
            return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
