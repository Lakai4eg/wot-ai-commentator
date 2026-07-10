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
from .server import AppContext, create_app
from .session_memory import SessionMemory
from .tts import SileroTTS
from .wotstat.client import DataProviderClient
from .wotstat.mapper import EventMapper

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
    memory = SessionMemory()
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

    ctx = AppContext(
        settings=settings,
        settings_path=SETTINGS_PATH,
        db=db,
        memory=memory,
        director=None,  # type: ignore[arg-type]
        backend=backend,
    )
    director = Director(settings, memory, backend, ctx.publish)
    ctx.director = director

    # TTS: загрузка модели в фоне, не блокируя старт
    def load_tts() -> None:
        tts = SileroTTS()
        ctx.tts = tts
        ctx.statuses["tts_status"] = "ready" if tts.available else "unavailable"

    asyncio.get_event_loop().run_in_executor(None, load_tts)
    ctx.statuses["tts_status"] = "loading"

    # Источник событий боя: WotStat DataProvider (клиент + маппер).
    # Клиент работает asyncio-таском в этом же лупе, коллбеки маппера зовут
    # director.submit напрямую (без потоков — всё в одном событийном лупе).
    client = DataProviderClient(settings.wotstat_url)
    mapper = EventMapper(client, submit=director.submit)

    # Чат
    router = ChatRouter(db, director, settings)
    chat = TwitchChatReader(settings.twitch_channel, router.handle)

    # LLM-статус
    def refresh_statuses() -> None:
        diag = mapper.diag
        ctx.statuses["wotstat"] = {
            "status": client.status,
            "game_state": diag["game_state"],
            "events_found": diag["events_found"],
            "last_event_at": client.last_event_at,
            "last_events": list(diag["last_events"]),
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
        asyncio.create_task(client.run()),
        asyncio.create_task(status_loop()),
    ]
    try:
        await server.serve()
    finally:
        director.stop()
        chat.stop()
        client.stop()
        for t in tasks:
            t.cancel()
        db.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
