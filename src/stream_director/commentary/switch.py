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

    def apply(self, data: dict) -> None:
        """Применить изменённые настройки к провайдерам (ключи/модели/URL).

        Смена ключа или адреса сбрасывает last_error — прежняя ошибка
        относилась к старым реквизитам.
        """
        if "gemini_api_key" in data:
            self.gemini.api_key = data["gemini_api_key"]
            self.gemini.last_error = None
        if "gemini_model" in data:
            self.gemini.model = data["gemini_model"]
        if "openai_base_url" in data:
            self.openai.base_url = data["openai_base_url"]
            self.openai.last_error = None
        if "openai_api_key" in data:
            self.openai.api_key = data["openai_api_key"]
            self.openai.last_error = None
        if "openai_model" in data:
            self.openai.model = data["openai_model"]

    async def generate(self, prompt: str, *, max_tokens: int = 80,
                       timeout_s: float | None = None) -> str | None:
        return await self.active.generate(prompt, max_tokens=max_tokens, timeout_s=timeout_s)

    async def close(self) -> None:
        await self.gemini.close()
        await self.openai.close()
