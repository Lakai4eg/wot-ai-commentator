"""Контракт игрового модуля и трекер активной игры.

Модуль игры — сборка из транспорта, памяти, описаний событий и колорита
для промпта. Контракт структурный (Protocol): реализации ничего не
наследуют, но тайпчекер проверяет соответствие. Ядро (директор, промпты,
сервер) работает только через этот контракт и ничего не знает про
конкретную игру.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..commentary.events import GameEvent
from ..stimulus import Stimulus


class GameSource(Protocol):
    """Клиент-транспорт игры: живёт всю сессию, переживает реконнекты."""

    status: str  # "connected" | "waiting"
    last_event_at: float | None

    async def run(self) -> None: ...
    def stop(self) -> None: ...


class GameMemory(Protocol):
    """Память игры: текущий бой + сессия."""

    def register(self, stimulus: Stimulus) -> list[str]: ...
    def battle_lines(self) -> list[str]: ...
    def session_lines(self) -> list[str]: ...
    def summary_lines(self) -> list[str]: ...
    def brief_subject(self) -> str | None: ...


@dataclass
class GameModule:
    id: str  # "wot" | "lol" — ключ маршрутизации стимулов
    display_name: str
    source: GameSource
    memory: GameMemory
    build_event: Callable[[Stimulus], GameEvent]
    # На чём играет стример прямо сейчас — тема брифа; None — ещё неизвестно.
    brief_subject: Callable[[], str | None]
    always_speak_types: frozenset[str]  # события в обход кулдауна
    diag: Callable[[], dict]  # диагностика маппера для /api/status
    # Подсказки «угла шутки» для промпта; None — модуль ротацию не использует.
    joke_angles: Callable[[], tuple[str, ...]] | None = None


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
