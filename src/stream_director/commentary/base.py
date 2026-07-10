"""Интерфейс подключаемого бэкенда комментариев."""

from __future__ import annotations

from typing import Protocol


class CommentaryBackend(Protocol):
    last_error: str | None

    async def generate(self, prompt: str) -> str | None:
        """Вернуть реплику или None при любом сбое (исключения не бросать)."""
        ...
