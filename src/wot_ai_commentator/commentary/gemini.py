"""Бэкенд комментариев: Google Gemini (REST, generateContent)."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiBackend:
    def __init__(self, api_key: str, model: str = "gemini-3-flash", timeout_s: float = 4.0):
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.last_error: str | None = None
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def generate(self, prompt: str) -> str | None:
        if not self.api_key:
            self.last_error = "API-ключ Gemini не задан"
            return None
        url = f"{_BASE_URL}/{self.model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 80, "thinkingConfig": {"thinkingBudget": 0}},
        }
        try:
            resp = await self._get_client().post(
                url,
                json=payload,
                headers={"x-goog-api-key": self.api_key},
                timeout=self.timeout_s,
            )
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                log.warning("Gemini error %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = str(text).strip()
            if not text:
                self.last_error = "пустой ответ"
                return None
            self.last_error = None
            return text
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("Gemini request failed: %s", self.last_error)
            return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
