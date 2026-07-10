"""Точка входа: сборка контейнера и запуск сервера + фоновых задач."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from .chat.router import ChatRouter
from .chat.twitch import TwitchChatReader
from .commentary.gemini import GeminiBackend
from .commentary.openai_compat import OpenAICompatBackend
from .commentary.switch import SwitchBackend
from .config import load_settings
from .db import WhitelistDB
from .director import Director
from .games.base import ActiveGameTracker
from .games.lol.module import build_module as build_lol_module
from .games.wot.module import build_module as build_wot_module
from .server import AppContext, create_app
from .tts import SileroTTS

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "wot-ai-commentator.db"


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    DATA_DIR.mkdir(exist_ok=True)

    settings = load_settings(SETTINGS_PATH)
    db = WhitelistDB(DB_PATH)
    backend = SwitchBackend(
        settings,
        GeminiBackend(settings.gemini_api_key, settings.gemini_model, settings.reply_timeout_s),
        OpenAICompatBackend(
            settings.openai_base_url,
            settings.openai_api_key,
            settings.openai_model,
            settings.reply_timeout_s,
        ),
    )

    tracker = ActiveGameTracker(default="wot")
    ctx = AppContext(
        settings=settings,
        settings_path=SETTINGS_PATH,
        db=db,
        director=None,  # type: ignore[arg-type]
        tracker=tracker,
        backend=backend,
    )
    director = Director(settings, backend, ctx.publish, tracker)
    ctx.director = director

    # Игровые модули: источники всегда запущены, активную игру решает трекер.
    wot = build_wot_module(settings, director.submit,
                           on_live=lambda: tracker.mark_live("wot"))
    director.register(wot)
    lol = build_lol_module(settings, director.submit,
                           on_live=lambda: tracker.mark_live("lol"))
    director.register(lol)

    # TTS: загрузка модели в фоне, не блокируя старт
    def load_tts() -> None:
        tts = SileroTTS(voice=settings.default_voice)
        ctx.tts = tts
        ctx.statuses["tts_status"] = "ready" if tts.available else "unavailable"

    asyncio.get_event_loop().run_in_executor(None, load_tts)
    ctx.statuses["tts_status"] = "loading"

    # Чат
    router = ChatRouter(db, director, settings)
    chat = TwitchChatReader(settings.twitch_channel, router.handle)

    # LLM-статус
    def refresh_statuses() -> None:
        wot_diag = wot.diag()
        ctx.statuses["wotstat"] = {
            "status": wot.source.status,
            "game_state": wot_diag["game_state"],
            "events_found": wot_diag["events_found"],
            "last_event_at": wot.source.last_event_at,
            "last_events": list(wot_diag["last_events"]),
        }
        lol_diag = lol.diag()
        ctx.statuses["lol"] = {
            "status": lol.source.status,
            "events_found": lol_diag["events_found"],
            "last_event_at": lol.source.last_event_at,
            "last_events": list(lol_diag["last_events"]),
        }
        ctx.statuses["chat"] = chat.status
        ctx.statuses["llm_last_error"] = backend.last_error
        ctx.statuses["llm_configured"] = backend.configured
        ctx.statuses["llm_provider"] = settings.llm_provider

    async def status_loop() -> None:
        while True:
            refresh_statuses()
            await asyncio.sleep(2)

    app = create_app(ctx)
    config = uvicorn.Config(app, host="127.0.0.1", port=settings.server_port, log_level="warning")
    server = uvicorn.Server(config)

    log.info("Панель:   http://127.0.0.1:%d/panel", settings.server_port)
    log.info("Оверлей:  http://127.0.0.1:%d/overlay  (добавь как browser source в OBS)", settings.server_port)

    tasks = [
        asyncio.create_task(director.run()),
        asyncio.create_task(chat.run()),
        asyncio.create_task(wot.source.run()),
        asyncio.create_task(lol.source.run()),
        asyncio.create_task(status_loop()),
    ]
    try:
        await server.serve()
    finally:
        director.stop()
        chat.stop()
        wot.source.stop()
        lol.source.stop()
        for t in tasks:
            t.cancel()
        db.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
