"""Настройки приложения: dataclass + сохранение в JSON."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # Активный LLM-провайдер: "gemini" | "openai" (любой OpenAI-совместимый API).
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    twitch_channel: str = ""
    text_enabled: bool = True
    voice_enabled: bool = True
    chat_commands_enabled: bool = True
    global_cooldown_s: float = 4.0
    # Дебаунс фраз: в буре мелких событий не частим — ждём паузу debounce_s,
    # чтобы всплеск схлопнулся в одну реплику про самое важное. Но не молчим
    # дольше debounce_max_s даже в непрерывном замесе. Крупные события (фраг,
    # смерть, пожар, детонация) и заказы из чата дебаунс не задерживает.
    debounce_s: float = 1.2
    debounce_max_s: float = 5.0
    user_cooldown_s: float = 60.0
    # WebSocket мода wotstat-data-provider — единственный источник событий боя.
    wotstat_url: str = "ws://localhost:38200"
    server_port: int = 8710
    reply_timeout_s: float = 4.0


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    known = {f.name for f in dataclasses.fields(Settings)}
    data = {k: v for k, v in raw.items() if k in known}
    try:
        return Settings(**data)
    except TypeError:
        log.warning("settings.json contains invalid values, using defaults")
        return Settings()


def save_settings(settings: Settings, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dataclasses.asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
