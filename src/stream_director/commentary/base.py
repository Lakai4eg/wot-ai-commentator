"""Интерфейс подключаемого бэкенда комментариев."""

from __future__ import annotations

from typing import Protocol


class CommentaryBackend(Protocol):
    last_error: str | None

    async def generate(self, prompt: str, *, max_tokens: int = 80,
                       timeout_s: float | None = None) -> str | None:
        """Вернуть текст или None при любом сбое (исключения не бросать).

        Дефолты рассчитаны на короткую реплику; длинным ответам (игровой бриф)
        нужны свои max_tokens и timeout_s — базовый таймаут бэкенда для них мал.
        """
        ...
