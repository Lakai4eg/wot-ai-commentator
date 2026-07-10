"""Стимулы (игровые события, чат-заказы, управляющие команды) и приоритеты."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


GAME_EVENT_TYPES = (
    "frag",
    "death",
    "ammo_rack",
    "oneshot",
    "damage_record",
    "battle_result",
)


@dataclass
class Stimulus:
    kind: str  # game_event | chat_order | control
    type: str
    priority: Priority = Priority.NORMAL
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    ttl_s: float = 20.0

    def expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self.created_at > self.ttl_s
