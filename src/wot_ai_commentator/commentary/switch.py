"""Переключатель LLM-провайдеров: выбирает активный бэкенд по настройке.

Директор держит одну ссылку на SwitchBackend; смена llm_provider в панели
меняет активный бэкенд на лету, без перезапуска.
"""

from __future__ import annotations

from ..config import Settings
from .gemini import GeminiBackend
from .openai_compat import OpenAICompatBackend


class SwitchBackend:
    def __init__(self, settings: Settings, gemini: GeminiBackend, openai: OpenAICompatBackend):
        self.settings = settings
        self.gemini = gemini
        self.openai = openai

    @property
    def active(self) -> GeminiBackend | OpenAICompatBackend:
        return self.openai if self.settings.llm_provider == "openai" else self.gemini

    @property
    def configured(self) -> bool:
        if self.settings.llm_provider == "openai":
            return self.openai.configured
        return bool(self.gemini.api_key)

    @property
    def last_error(self) -> str | None:
        return self.active.last_error

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        self.active.last_error = value

    async def generate(self, prompt: str) -> str | None:
        return await self.active.generate(prompt)

    async def close(self) -> None:
        await self.gemini.close()
        await self.openai.close()
