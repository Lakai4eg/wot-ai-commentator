"""FastAPI-сервер: REST API, WebSocket оверлея, статика React-сборки."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .commentary.gemini import GeminiBackend
from .commentary.switch import SwitchBackend
from .config import Settings, save_settings
from .db import ROLES, WhitelistDB
from .director import Director
from .events import Stimulus
from .session_memory import SessionMemory
from .tts import AudioStore, SileroTTS

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    settings_path: Path
    db: WhitelistDB
    memory: SessionMemory
    director: Director
    backend: SwitchBackend | GeminiBackend | None = None
    audio: AudioStore = field(default_factory=AudioStore)
    tts: SileroTTS | None = None
    replica_counter: int = 0
    statuses: dict[str, Any] = field(default_factory=dict)
    ws_clients: set[WebSocket] = field(default_factory=set)

    async def publish(self, text: str, stimulus: Stimulus) -> None:
        """Колбэк директора: реплика → все WS-клиенты (+ озвучка)."""
        self.replica_counter += 1
        replica_id = self.replica_counter
        message: dict[str, Any] = {
            "type": "replica",
            "id": replica_id,
            "text": text if self.settings.text_enabled else "",
            "effect": stimulus.type,
        }
        # Текст уходит сразу; озвучка догоняет отдельным сообщением.
        if self.settings.voice_enabled and self.tts is not None and self.tts.available:
            asyncio.get_running_loop().create_task(self._send_audio(replica_id, text))
        elif not self.settings.text_enabled:
            return
        dead = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    async def _send_audio(self, replica_id: int, text: str) -> None:
        try:
            wav = await asyncio.to_thread(self.tts.synth, text)
        except Exception:
            return
        if not wav:
            return
        message = {
            "type": "audio",
            "replica_id": replica_id,
            "audio_url": f"/api/audio/{self.audio.put(wav)}",
        }
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.ws_clients.discard(ws)


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
    global_cooldown_s: float | None = None
    debounce_s: float | None = None
    debounce_max_s: float | None = None
    user_cooldown_s: float | None = None


def _masked_settings(s: Settings) -> dict:
    data = dataclasses.asdict(s)
    for key in ("gemini_api_key", "openai_api_key"):
        if data[key]:
            data[key] = "•" * 8
    return data


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="WoT AI Commentator")

    @app.get("/api/settings")
    async def get_settings():
        return _masked_settings(ctx.settings)

    @app.put("/api/settings")
    async def put_settings(patch: SettingsIn):
        data = patch.model_dump(exclude_none=True)
        if "llm_provider" in data and data["llm_provider"] not in ("gemini", "openai"):
            raise HTTPException(400, "llm_provider must be 'gemini' or 'openai'")
        for key, value in data.items():
            setattr(ctx.settings, key, value)
        if ctx.backend is not None:
            gemini = getattr(ctx.backend, "gemini", ctx.backend)
            openai = getattr(ctx.backend, "openai", None)
            if "gemini_api_key" in data:
                gemini.api_key = data["gemini_api_key"]
                gemini.last_error = None
            if "gemini_model" in data:
                gemini.model = data["gemini_model"]
            if openai is not None:
                if "openai_base_url" in data:
                    openai.base_url = data["openai_base_url"]
                    openai.last_error = None
                if "openai_api_key" in data:
                    openai.api_key = data["openai_api_key"]
                    openai.last_error = None
                if "openai_model" in data:
                    openai.model = data["openai_model"]
            # Статус LLM обновляем сразу, не дожидаясь status_loop (2 с).
            ctx.statuses["llm_provider"] = ctx.settings.llm_provider
            if hasattr(ctx.backend, "configured"):
                ctx.statuses["llm_configured"] = ctx.backend.configured
            ctx.statuses["llm_last_error"] = ctx.backend.last_error
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
        return {
            **ctx.statuses,
            "overlay_clients": len(ctx.ws_clients),
            "director": ctx.director.stats(),
            "tts": bool(ctx.tts and ctx.tts.available),
            "memory": ctx.memory.summary_lines(),
        }

    @app.get("/api/audio/{audio_id}")
    async def get_audio(audio_id: str):
        wav = ctx.audio.get(audio_id)
        if wav is None:
            raise HTTPException(404, "audio not found")
        return Response(content=wav, media_type="audio/wav")

    @app.websocket("/ws/overlay")
    async def ws_overlay(ws: WebSocket):
        await ws.accept()
        ctx.ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # ping от клиента / держим соединение
        except WebSocketDisconnect:
            pass
        finally:
            ctx.ws_clients.discard(ws)

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
