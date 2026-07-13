"""Точка входа: сборка контейнера и запуск сервера + фоновых задач."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import webbrowser
from pathlib import Path
from typing import Awaitable, Callable

import uvicorn

from . import __version__
from .broadcast import OverlayBroadcaster
from .chat.router import ChatRouter
from .chat.twitch import TwitchChatReader
from .commentary.brief import BriefGenerator
from .commentary.gemini import GeminiBackend
from .commentary.openai_compat import OpenAICompatBackend
from .commentary.switch import SwitchBackend
from .config import load_settings
from .db import ChatUserDB, Database, PromptStore
from .director import Director
from .games.base import ActiveGameTracker
from .games.lol.module import build_module as build_lol_module
from .games.wot.module import build_module as build_wot_module
from .paths import DATA_DIR, DB_PATH, INSTALL, SETTINGS_PATH, migrate_state
from .server import AppContext, create_app
from .tts import ChatterboxTTS
from .update_check import apply_update_status

log = logging.getLogger(__name__)

# БД под прежними именами приложения — подхватываем самую свежую.
LEGACY_DB_NAMES = ("wot-ai-commentator.db", "stream-director.db")


def migrate_legacy_db(data_dir: Path = DATA_DIR, target: Path = DB_PATH) -> None:
    """Переименовать БД от старого имени приложения, если новой ещё нет."""
    if target.exists():
        return
    legacy = [p for name in LEGACY_DB_NAMES if (p := data_dir / name).exists()]
    if not legacy:
        return
    newest = max(legacy, key=lambda p: p.stat().st_mtime)
    log.info("Мигрирую БД: %s → %s", newest.name, target.name)
    newest.rename(target)


def mark_known_good() -> None:
    """Версия поднялась — фиксируем её как рабочую и убираем остальные.

    Лаунчер предлагает откат, пока known-good не равен current, поэтому старая
    версия обязана дожить до этого момента — удаляем её только здесь. Вне
    дистрибутива (разработка) версиями никто не управляет, делать нечего.
    """
    if INSTALL is None:
        return
    try:
        current = (INSTALL / "current.txt").read_text(encoding="utf-8").strip()
        if current != __version__:
            # Указатель смотрит не на нас — значит, раскладка в рассогласовании
            # (например, апдейтер упал на полпути). Убирать версии в такой
            # ситуации нельзя: снесём ровно ту, которую лаунчер запустит следом.
            log.warning("current.txt = %s, а работает %s — версии не трогаю",
                        current, __version__)
            return
        (INSTALL / "known-good.txt").write_text(__version__, encoding="utf-8")
        versions = INSTALL / "versions"
        if versions.is_dir():
            for entry in versions.iterdir():
                if entry.is_dir() and entry.name != __version__:
                    shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        log.warning("не удалось записать known-good.txt", exc_info=True)


async def supervised(name: str, run: Callable[[], Awaitable[None]],
                     retry_s: float = 5.0) -> None:
    """Фоновая задача не должна умирать молча: упала — лог и перезапуск.

    Штатное завершение (после stop()) и отмена проходят как есть.
    """
    while True:
        try:
            await run()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Задача «%s» упала — перезапуск через %.0f с", name, retry_s)
            await asyncio.sleep(retry_s)


async def open_panel_when_ready(
    server, url: str, opener: Callable[[str], object] | None = None
) -> None:
    """Открыть панель, когда сервер реально принимает соединения.

    Лаунчер portable-сборки выставляет STREAM_DIRECTOR_OPEN_PANEL=1 — браузер
    открывает не он, а мы: так на медленном первом старте пользователь не
    увидит «connection refused».
    """
    open_url = opener or webbrowser.open
    while not server.started:
        await asyncio.sleep(0.2)
    open_url(url)


async def mark_known_good_when_ready(server) -> None:
    """Сервер принимает соединения — значит, версия рабочая."""
    while not server.started:
        await asyncio.sleep(0.2)
    mark_known_good()


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    migrate_state()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    migrate_legacy_db()

    settings = load_settings(SETTINGS_PATH)
    database = Database(DB_PATH)
    db = ChatUserDB(database)
    store = PromptStore(database)
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
    broadcaster = OverlayBroadcaster(settings)
    briefs = BriefGenerator(backend, store, settings)
    director = Director(settings, backend, broadcaster.publish, tracker, store, briefs)

    # Чат
    router = ChatRouter(db, director, settings)
    chat = TwitchChatReader(settings.twitch_channel, router.handle)

    ctx = AppContext(
        settings=settings,
        settings_path=SETTINGS_PATH,
        db=db,
        director=director,
        broadcaster=broadcaster,
        tracker=tracker,
        backend=backend,
        chat=chat,
        store=store,
        briefs=briefs,
    )

    # Игровые модули: источники всегда запущены, активную игру решает трекер.
    wot = build_wot_module(settings, director.submit,
                           on_live=lambda: tracker.mark_live("wot"))
    director.register(wot)
    lol = build_lol_module(settings, director.submit,
                           on_live=lambda: tracker.mark_live("lol"))
    director.register(lol)

    # Голос: bootstrap (GPU-проверка, докачки) и запуск worker-а — в фоне,
    # сервер и текстовые реплики доступны сразу.
    def set_tts_status(st: dict) -> None:
        ctx.statuses["tts_state"] = st
        ctx.statuses["tts_status"] = st["state"]

    tts = ChatterboxTTS(on_status=set_tts_status)
    broadcaster.tts = tts
    asyncio.get_running_loop().run_in_executor(None, tts.start)

    # Диагностика источников/чата/LLM — вызывается на каждый GET /api/status.
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

    ctx.refresh_status = refresh_statuses

    app = create_app(ctx)
    config = uvicorn.Config(app, host="127.0.0.1", port=settings.server_port, log_level="warning")
    server = uvicorn.Server(config)

    log.info("Панель:   http://127.0.0.1:%d/panel", settings.server_port)
    log.info("Оверлей:  http://127.0.0.1:%d/overlay  (добавь как browser source в OBS)", settings.server_port)

    tasks = [
        asyncio.create_task(supervised("director", director.run)),
        asyncio.create_task(supervised("chat", chat.run)),
        asyncio.create_task(supervised("wot", wot.source.run)),
        asyncio.create_task(supervised("lol", lol.source.run)),
        # Одноразовая проверка обновлений: без supervised — сама глушит ошибки.
        asyncio.create_task(apply_update_status(ctx.statuses, __version__)),
        asyncio.create_task(mark_known_good_when_ready(server)),
    ]
    # Portable-лаунчер просит открыть панель в браузере после старта сервера.
    if os.environ.get("STREAM_DIRECTOR_OPEN_PANEL") == "1":
        tasks.append(
            asyncio.create_task(
                open_panel_when_ready(
                    server, f"http://127.0.0.1:{settings.server_port}/panel"
                )
            )
        )
    try:
        await server.serve()
    finally:
        tts.stop()
        director.stop()
        chat.stop()
        wot.source.stop()
        lol.source.stop()
        for t in tasks:
            t.cancel()
        await backend.close()
        database.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
