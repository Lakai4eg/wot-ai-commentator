"""Сборка игрового модуля WoT: клиент + маппер + память + колорит."""

from __future__ import annotations

from typing import Callable

from ...config import Settings
from ...stimulus import Stimulus
from ..base import GameModule
from .client import WotStatClient
from .flavor import describe_event, fallback_line, flavor_lines
from .mapper import WotMapper
from .memory import WotSessionMemory


def build_module(
    settings: Settings,
    submit: Callable[[Stimulus], None],
    on_live: Callable[[], None] | None = None,
) -> GameModule:
    client = WotStatClient(settings.wotstat_url, on_live=on_live)
    mapper = WotMapper(client, submit=submit)
    return GameModule(
        id="wot",
        display_name="Мир танков",
        source=client,
        memory=WotSessionMemory(),
        describe_event=describe_event,
        flavor_lines=flavor_lines,
        fallback_line=fallback_line,
        always_speak_types=frozenset({"death"}),
        diag=lambda: mapper.diag,
    )
