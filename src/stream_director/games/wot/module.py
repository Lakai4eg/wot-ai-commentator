"""Сборка игрового модуля WoT: клиент + маппер + память + колорит."""

from __future__ import annotations

from typing import Callable

from ...config import Settings
from ...stimulus import Stimulus
from ..base import GameModule
from .client import WotStatClient
from .flavor import build_event
from .mapper import WotMapper
from .memory import WotSessionMemory


def build_module(
    settings: Settings,
    submit: Callable[[Stimulus], None],
    on_live: Callable[[], None] | None = None,
) -> GameModule:
    client = WotStatClient(settings.wotstat_url, on_live=on_live)
    mapper = WotMapper(client, submit=submit)
    memory = WotSessionMemory()
    return GameModule(
        id="wot",
        display_name="Мир танков",
        source=client,
        memory=memory,
        build_event=build_event,
        brief_subject=lambda: memory.brief_subject(),
        always_speak_types=frozenset({"death"}),
        diag=lambda: mapper.diag,
    )
