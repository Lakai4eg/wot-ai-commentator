"""FastAPI-сервер: REST API, WebSocket оверлея, статика React-сборки."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .broadcast import OverlayBroadcaster
from .commentary.switch import SwitchBackend
from .config import Settings, save_settings
from .db import ROLES, ChatUserDB
from .director import Director
from .games.base import ActiveGameTracker
from .tts import VOICES

log = logging.getLogger(__name__)


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
    debounce_s: float | None = None
    debounce_max_s: float | None = None
    user_cooldown_s: float | None = None
    tts_max_age_s: float | None = None
    default_voice: str | None = None
    voice_by_priority: dict[str, str] | None = None
    voice_overrides: dict[str, str] | None = None


class PreviewIn(BaseModel):
    voice: str | None = None
    text: str | None = None


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
        for key in ("global_cooldown_s", "debounce_s", "debounce_max_s",
                    "user_cooldown_s", "tts_max_age_s"):
            if key in data and data[key] < 0:
                raise HTTPException(400, f"{key} must be >= 0")
        for key, value in data.items():
            setattr(ctx.settings, key, value)
        if ctx.backend is not None:
            ctx.backend.apply(data)
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
    async def list_voices():
        return {"voices": list(VOICES)}

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
