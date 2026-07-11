"""Сборка игрового модуля LoL: поллер + маппер + память + колорит."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...config import Settings
from ...stimulus import Stimulus
from ..base import GameModule
from ..template_pool import TemplatePool
from .client import LiveClientPoller
from .event_log import LolEventLog
from .flavor import describe_event, flavor_lines, joke_angles, variant_key
from .mapper import LolMapper
from .memory import LolSessionMemory

_TEMPLATES_DIR = Path(__file__).parent / "templates"


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
    # Пул на время жизни модуля = сессия приложения: сброс — перезапуск.
    pool = TemplatePool(_TEMPLATES_DIR, variant_key)
    return GameModule(
        id="lol",
        display_name="League of Legends",
        source=client,
        memory=LolSessionMemory(),
        describe_event=describe_event,
        flavor_lines=flavor_lines,
        fallback_line=lambda s: pool.take(s) or pool.exhausted_pick(s),
        always_speak_types=frozenset({"battle_start", "death", "multikill"}),
        diag=lambda: mapper.diag,
        joke_angles=joke_angles,
        template_pool=pool,
    )
