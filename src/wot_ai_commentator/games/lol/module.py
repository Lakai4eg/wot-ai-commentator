"""Сборка игрового модуля LoL: поллер + маппер + память + колорит."""

from __future__ import annotations

from typing import Callable

from ...config import Settings
from ...events import Stimulus
from ..base import GameModule
from .client import LiveClientPoller
from .event_log import LolEventLog
from .flavor import describe_event, fallback_line, flavor_lines
from .mapper import LolMapper
from .memory import LolSessionMemory


def build_module(
    settings: Settings,
    submit: Callable[[Stimulus], None],
    on_live: Callable[[], None] | None = None,
) -> GameModule:
    mapper = LolMapper(submit=submit, event_log=LolEventLog())
    client = LiveClientPoller(
        getattr(settings, "lol_url", "https://127.0.0.1:2999"),
        on_payload=mapper.handle_payload,
        on_live=on_live,
    )
    return GameModule(
        id="lol",
        display_name="League of Legends",
        source=client,
        memory=LolSessionMemory(),
        describe_event=describe_event,
        flavor_lines=flavor_lines,
        fallback_line=fallback_line,
        always_speak_types=frozenset({"battle_start", "death", "multikill"}),
        diag=lambda: mapper.diag,
    )
