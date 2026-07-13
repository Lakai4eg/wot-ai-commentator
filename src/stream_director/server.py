"""FastAPI-сервер: REST API, WebSocket оверлея, статика React-сборки."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .broadcast import OverlayBroadcaster
from .chat.twitch import TwitchChatReader
from .commentary.brief import BriefGenerator
from .commentary.defaults import RESPONSE_FORMAT_KEY, game_base_key
from .commentary.switch import SwitchBackend
from .config import Settings, save_settings
from .db import ROLES, ChatUserDB, PromptStore
from .director import Director
from .games.base import ActiveGameTracker
from .tts import DEFAULT_VOICE, EMOTION_MARKERS, delete_voice, list_voices, save_voice

log = logging.getLogger(__name__)

GAMES = ("wot", "lol")


@dataclass
class AppContext:
    """Собранные зависимости приложения — то, что нужно эндпоинтам."""

    settings: Settings
    settings_path: Path
    db: ChatUserDB
    director: Director
    broadcaster: OverlayBroadcaster
    tracker: ActiveGameTracker | None = None
    backend: SwitchBackend | None = None
    chat: TwitchChatReader | None = None
    store: PromptStore | None = None
    briefs: BriefGenerator | None = None
    statuses: dict[str, Any] = field(default_factory=dict)
    # Свежая диагностика по запросу /api/status — вместо фонового полла.
    refresh_status: Callable[[], None] | None = None


class UserIn(BaseModel):
    username: str
    role: str = "director"


class SettingsIn(BaseModel):
    llm_provider: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    twitch_channel: str | None = None
    text_enabled: bool | None = None
    voice_enabled: bool | None = None
    chat_commands_enabled: bool | None = None
    commands_open_to_all: bool | None = None
    global_cooldown_s: float | None = None
    debounce_window_s: float | None = None
    user_cooldown_s: float | None = None
    tts_max_age_s: float | None = None
    active_persona_id: int | None = None
    default_voice: str | None = None
    voice_by_priority: dict[str, str] | None = None
    voice_overrides: dict[str, str] | None = None
    voice_by_marker: dict[str, str] | None = None


class PreviewIn(BaseModel):
    voice: str | None = None
    text: str | None = None


class PersonaIn(BaseModel):
    name: str
    text: str


class PersonaPatch(BaseModel):
    name: str | None = None
    text: str | None = None


class TextIn(BaseModel):
    text: str


def _check_game(game: str) -> None:
    if game not in GAMES:
        raise HTTPException(404, f"unknown game {game!r}")


def _masked_settings(s: Settings) -> dict:
    data = dataclasses.asdict(s)
    for key in ("gemini_api_key", "openai_api_key"):
        if data[key]:
            data[key] = "•" * 8
    return data


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Stream Director")

    @app.get("/api/settings")
    async def get_settings():
        return _masked_settings(ctx.settings)

    @app.put("/api/settings")
    async def put_settings(patch: SettingsIn):
        data = patch.model_dump(exclude_none=True)
        if "llm_provider" in data and data["llm_provider"] not in ("gemini", "openai"):
            raise HTTPException(400, "llm_provider must be 'gemini' or 'openai'")
        # Маска из GET («••••••••») — не настоящий ключ: молча игнорируем,
        # чтобы случайный PUT маски не затёр сохранённый ключ.
        for key in ("gemini_api_key", "openai_api_key"):
            value = data.get(key)
            if value and set(value) == {"•"}:
                data.pop(key)
        for key in ("global_cooldown_s", "debounce_window_s",
                    "user_cooldown_s", "tts_max_age_s"):
            if key in data and data[key] < 0:
                raise HTTPException(400, f"{key} must be >= 0")
        for key, value in data.items():
            setattr(ctx.settings, key, value)
        if ctx.backend is not None:
            ctx.backend.apply(data)
        if "twitch_channel" in data and ctx.chat is not None:
            ctx.chat.set_channel(data["twitch_channel"])
        save_settings(ctx.settings, ctx.settings_path)
        return _masked_settings(ctx.settings)

    @app.post("/api/llm/test")
    async def llm_test():
        """Пробный запрос к активному LLM — панель зовёт после смены модели."""
        if ctx.backend is None:
            raise HTTPException(503, "backend not ready")
        text = await ctx.backend.generate(
            "Ты закадровый комментатор стрима. Ответь одной короткой фразой, что ты на связи."
        )
        ctx.statuses["llm_last_error"] = ctx.backend.last_error
        return {"ok": text is not None, "reply": text, "error": ctx.backend.last_error}

    @app.get("/api/prompts")
    async def get_prompts():
        store = ctx.store
        games = {}
        for game in GAMES:
            brief = store.get_brief(game)
            games[game] = {
                "base": store.get_prompt(game_base_key(game)),
                "base_customized": store.is_customized(game_base_key(game)),
                "brief": brief.text if brief else "",
                "subject": brief.subject if brief else "",
                "generated_at": brief.generated_at if brief else "",
                "error": (ctx.briefs.last_error.get(game) if ctx.briefs else None),
            }
        return {
            "personas": store.list_personas(),
            "active_persona_id": ctx.settings.active_persona_id,
            "response_format": store.get_prompt(RESPONSE_FORMAT_KEY),
            "response_format_customized": store.is_customized(RESPONSE_FORMAT_KEY),
            "games": games,
        }

    @app.post("/api/personas", status_code=201)
    async def create_persona(body: PersonaIn):
        try:
            persona_id = ctx.store.create_persona(body.name, body.text)
        except (ValueError, sqlite3.IntegrityError) as e:
            raise HTTPException(400, str(e))
        return {"id": persona_id}

    @app.put("/api/personas/{persona_id}")
    async def update_persona(persona_id: int, body: PersonaPatch):
        if not ctx.store.update_persona(persona_id, body.name, body.text):
            raise HTTPException(404, "persona not found")
        return {"ok": True}

    @app.delete("/api/personas/{persona_id}")
    async def delete_persona(persona_id: int):
        if not ctx.store.delete_persona(persona_id):
            raise HTTPException(400, "встроенный пресет не удаляется")
        if ctx.settings.active_persona_id == persona_id:
            builtin = next((p for p in ctx.store.list_personas() if p["is_builtin"]), None)
            ctx.settings.active_persona_id = builtin["id"] if builtin else 1
            save_settings(ctx.settings, ctx.settings_path)
        return {"ok": True}

    @app.post("/api/personas/{persona_id}/reset")
    async def reset_persona(persona_id: int):
        if not ctx.store.reset_persona(persona_id):
            raise HTTPException(400, "сбрасывается только встроенный пресет")
        return {"ok": True}

    @app.put("/api/prompts/response_format")
    async def put_response_format(body: TextIn):
        ctx.store.set_prompt(RESPONSE_FORMAT_KEY, body.text)
        return {"ok": True}

    @app.post("/api/prompts/response_format/reset")
    async def reset_response_format():
        ctx.store.reset_prompt(RESPONSE_FORMAT_KEY)
        return {"text": ctx.store.get_prompt(RESPONSE_FORMAT_KEY)}

    @app.put("/api/prompts/game/{game}/base")
    async def put_game_base(game: str, body: TextIn):
        _check_game(game)
        ctx.store.set_prompt(game_base_key(game), body.text)
        return {"ok": True}

    @app.post("/api/prompts/game/{game}/base/reset")
    async def reset_game_base(game: str):
        _check_game(game)
        ctx.store.reset_prompt(game_base_key(game))
        return {"text": ctx.store.get_prompt(game_base_key(game))}

    @app.put("/api/prompts/game/{game}/brief")
    async def put_game_brief(game: str, body: TextIn):
        _check_game(game)
        current = ctx.store.get_brief(game)
        subject = current.subject if current else ""
        if not subject:
            # Правка до первой генерации: тему берём у модуля. Иначе директор,
            # сверяющий тему брифа с текущей техникой, счёл бы бриф чужим.
            module = ctx.director.games.get(game)
            subject = (module.brief_subject() or "") if module else ""
        ctx.store.save_brief(game, subject, body.text)
        return {"ok": True}

    @app.post("/api/prompts/game/{game}/brief/regenerate")
    async def regenerate_brief(game: str):
        _check_game(game)
        module = ctx.director.games.get(game)
        if module is None or ctx.briefs is None:
            raise HTTPException(503, "модуль игры не готов")
        text = await ctx.briefs.generate(module)
        if text is None:
            raise HTTPException(503, ctx.briefs.last_error.get(game) or "не удалось")
        brief = ctx.store.get_brief(game)
        return {"brief": brief.text, "subject": brief.subject,
                "generated_at": brief.generated_at}

    @app.get("/api/users")
    async def list_users():
        return ctx.db.list_users()

    @app.post("/api/users", status_code=201)
    async def add_user(user: UserIn):
        if user.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        if not user.username.strip():
            raise HTTPException(400, "username is empty")
        ctx.db.add_user(user.username, role=user.role)
        return {"ok": True}

    @app.delete("/api/users/{platform}/{username}")
    async def delete_user(platform: str, username: str):
        if not ctx.db.remove_user(username, platform=platform):
            raise HTTPException(404, "user not found")
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        if ctx.refresh_status is not None:
            ctx.refresh_status()
        active = ctx.tracker.active if ctx.tracker else "wot"
        module = ctx.director.games.get(active)
        tts = ctx.broadcaster.tts
        return {
            **ctx.statuses,
            "active_game": active,
            "app_version": __version__,
            "overlay_clients": len(ctx.broadcaster.ws_clients),
            "director": ctx.director.stats(),
            "tts": bool(tts and tts.available),
            "memory": module.memory.summary_lines() if module else [],
        }

    @app.get("/api/audio/{audio_id}")
    async def get_audio(audio_id: str):
        wav = ctx.broadcaster.audio.get(audio_id)
        if wav is None:
            raise HTTPException(404, "audio not found")
        return Response(content=wav, media_type="audio/wav")

    @app.get("/api/voices")
    async def get_voices():
        return {"voices": list_voices(), "markers": list(EMOTION_MARKERS)}

    @app.post("/api/voices", status_code=201)
    async def add_voice(name: str = Form(...), file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > 15 * 2**20:
            raise HTTPException(400, "референс больше 15 МБ")
        if not data.startswith(b"RIFF"):
            raise HTTPException(400, "нужен WAV-файл (около 10 секунд чистой речи)")
        try:
            save_voice(name, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.delete("/api/voices/{name}")
    async def remove_voice(name: str):
        if name == DEFAULT_VOICE:
            raise HTTPException(400, "встроенный голос не удаляется")
        if not delete_voice(name):
            raise HTTPException(404, "голос не найден")
        return {"ok": True}

    @app.post("/api/tts/retry", status_code=202)
    async def tts_retry():
        tts = ctx.broadcaster.tts
        if tts is None:
            raise HTTPException(503, "голос не инициализирован")
        asyncio.get_running_loop().run_in_executor(None, tts.start)
        return {"ok": True}

    @app.post("/api/tts/preview")
    async def preview_voice(body: PreviewIn):
        tts = ctx.broadcaster.tts
        if tts is None or not tts.available:
            raise HTTPException(503, "TTS недоступен")
        text = (body.text or "").strip() or "Проверка голоса. Раз, два, три."
        wav = await asyncio.to_thread(tts.synth, text, body.voice)
        if not wav:
            raise HTTPException(503, "не удалось синтезировать")
        return Response(content=wav, media_type="audio/wav")

    @app.websocket("/ws/overlay")
    async def ws_overlay(ws: WebSocket):
        await ws.accept()
        ctx.broadcaster.ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # ping от клиента / держим соединение
        except WebSocketDisconnect:
            pass
        finally:
            ctx.broadcaster.ws_clients.discard(ws)

    dist = Path(__file__).parent.parent.parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")

        @app.exception_handler(404)
        async def spa_fallback(request, exc):
            # /overlay и /panel — html-страницы vite multi-page
            path = request.url.path.strip("/")
            candidate = dist / f"{path}.html"
            if candidate.is_file():
                return FileResponse(candidate)
            return Response(status_code=404)

    return app
