"""Настройки приложения: dataclass + сохранение в JSON."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
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
    # Открытый режим: команды (напр. !dir) доступны любому зрителю, кроме тех,
    # кому выдана роль banned. Выключено — работает только белый список.
    commands_open_to_all: bool = False
    global_cooldown_s: float = 4.0
    # Окно склейки: первое игровое событие открывает окно, все события, попавшие
    # в него, уходят в LLM одним промптом и дают ОДНУ реплику. Крупные события
    # (смерть, мультикилл) закрывают окно немедленно. Чат-заказы !dir идут мимо.
    debounce_window_s: float = 3.0
    user_cooldown_s: float = 60.0
    # Активный пресет персоны (id в таблице personas); 1 — встроенный.
    active_persona_id: int = 1
    # WebSocket мода wotstat-data-provider — источник событий боя WoT.
    wotstat_url: str = "ws://localhost:38200"
    # Riot Live Client Data API — локальный HTTPS живого матча LoL.
    lol_url: str = "https://127.0.0.1:2999"
    server_port: int = 8710
    reply_timeout_s: float = 4.0
    # Голос не догоняет: если с момента события прошло больше tts_max_age_s,
    # реплику показываем текстом, но не озвучиваем (устаревшая реакция в эфире
    # звучит нелепо). Текст оверлея это не трогает.
    tts_max_age_s: float = 20.0
    # Озвучка: голос по умолчанию + правила «контекст → голос».
    # voice_by_priority: "low"/"normal"/"high"/"critical" → голос.
    # voice_overrides: точный stimulus.type → голос (важнее правила по приоритету).
    default_voice: str = "baya"
    voice_by_priority: dict[str, str] = field(default_factory=dict)
    voice_overrides: dict[str, str] = field(default_factory=dict)


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    known = {f.name for f in dataclasses.fields(Settings)}
    data = {k: v for k, v in raw.items() if k in known}
    try:
        settings = Settings(**data)
    except TypeError:
        log.warning("settings.json contains invalid values, using defaults")
        return Settings()
    return settings


def save_settings(settings: Settings, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dataclasses.asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
