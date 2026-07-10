"""Контракт игрового модуля и трекер активной игры.

Модуль игры — duck-typed сборка (в духе того, как EventMapper принимает
клиента): транспорт, память, описания событий и колорит для промпта,
шаблоны-фолбэки. Ядро (директор, промпты, сервер) работает только через
этот контракт и ничего не знает про конкретную игру.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..events import Stimulus


@dataclass
class GameModule:
    id: str  # "wot" | "lol" — ключ маршрутизации стимулов
    display_name: str
    source: Any  # клиент-транспорт: run() / stop() / status / last_event_at
    memory: Any  # register(stim) / battle_lines() / session_lines() / summary_lines()
    describe_event: Callable[[Stimulus], str]
    flavor_lines: Callable[[], str]  # сленг/мишени игры — блок в промпт
    fallback_line: Callable[[Stimulus], str | None]  # шаблон при мёртвой LLM
    always_speak_types: frozenset[str]  # события в обход кулдауна
    diag: Callable[[], dict]  # диагностика маппера для /api/status


class ActiveGameTracker:
    """Какую игру комментируем: последняя ожившая побеждает.

    Источники обеих игр всегда запущены; игра становится активной, когда её
    source оживает (WoT — init по WebSocket, LoL — ответил порт 2999), и
    остаётся активной, пока не оживёт другая: между матчами LoL порт умирает,
    но чат-заказы продолжают относиться к LoL.
    """

    def __init__(self, default: str = "wot") -> None:
        self.active = default

    def mark_live(self, game_id: str) -> None:
        self.active = game_id
