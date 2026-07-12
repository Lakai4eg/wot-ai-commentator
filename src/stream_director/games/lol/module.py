"""Сборка игрового модуля LoL: поллер + маппер + память + колорит."""

from __future__ import annotations

from typing import Callable

from ...config import Settings
from ...stimulus import Stimulus
from ..base import GameModule
from .client import LiveClientPoller
from .event_log import LolEventLog
from .flavor import build_event, joke_angles
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
    memory = LolSessionMemory()
    return GameModule(
        id="lol",
        display_name="League of Legends",
        source=client,
        memory=memory,
        build_event=build_event,
        brief_subject=lambda: memory.brief_subject(),
        always_speak_types=frozenset({"battle_start", "death", "multikill"}),
        diag=lambda: mapper.diag,
        joke_angles=joke_angles,
    )
