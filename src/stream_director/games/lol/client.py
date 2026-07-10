"""Поллер Riot Live Client Data API (локальный API живого матча LoL).

API поднимается самой игрой на https://127.0.0.1:2999 ТОЛЬКО во время матча
(сертификат самоподписанный — verify=False). Один эндпоинт
`/liveclientdata/allgamedata` отдаёт всё: activePlayer, allPlayers,
журнал events (append-only, с EventID) и gameData.

Поллер — только транспорт: держит статус, отдаёт каждый свежий снапшот в
коллбек on_payload и живёт всю сессию (между матчами порт мёртв — ждём).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)


class LiveClientPoller:
    PATH = "/liveclientdata/allgamedata"

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:2999",
        on_payload: Callable[[dict], None] | None = None,
        on_live: Callable[[], None] | None = None,
        poll_in_game_s: float = 1.0,
        poll_waiting_s: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.on_payload = on_payload
        self.on_live = on_live
        self.poll_in_game_s = poll_in_game_s
        self.poll_waiting_s = poll_waiting_s
        # статус "connected" — порт отвечает (идёт матч) | "waiting"
        self.status: str = "waiting"
        self.last_event_at: float | None = None
        self._running = False

    # --- обработка снапшота (public для тестов) ---

    def handle_payload(self, data: Any) -> None:
        """Свежий снапшот → коллбек; упавший коллбек не роняет поллер."""
        self.last_event_at = time.time()
        if self.on_payload is None or not isinstance(data, dict):
            return
        try:
            self.on_payload(data)
        except Exception:  # шоу продолжается
            log.exception("LoL: on_payload-коллбек упал")

    def _mark_live(self) -> None:
        if self.status != "connected":
            self.status = "connected"
            log.info("LoL: матч обнаружен, порт 2999 отвечает")
            if self.on_live is not None:
                try:
                    self.on_live()
                except Exception:
                    log.exception("LoL: on_live-коллбек упал")

    # --- полл-луп ---

    async def run(self) -> None:
        """Жить всю сессию: 1 с в матче, ~3 с в ожидании порта."""
        self._running = True
        async with httpx.AsyncClient(verify=False, timeout=2.0) as client:
            while self._running:
                try:
                    r = await client.get(self.base_url + self.PATH)
                    if r.status_code == 200:
                        self._mark_live()
                        self.handle_payload(r.json())
                        await asyncio.sleep(self.poll_in_game_s)
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass  # порт мёртв/битый ответ — обычное состояние между матчами
                self.status = "waiting"
                await asyncio.sleep(self.poll_waiting_s)

    def stop(self) -> None:
        self._running = False
