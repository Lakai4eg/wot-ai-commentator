"""Вещание реплик в оверлей: WebSocket-клиенты + догоняющая озвучка."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .config import Settings
from .stimulus import Stimulus
from .tts import AudioStore, ChatterboxTTS, parse, pick_voice

log = logging.getLogger(__name__)


@dataclass
class OverlayBroadcaster:
    """Колбэк директора: реплика → все WS-клиенты (+ озвучка).

    Текст уходит сразу; озвучка догоняет отдельным сообщением — но только
    если реплика не запоздала (см. _voice_fresh).
    """

    settings: Settings
    audio: AudioStore = field(default_factory=AudioStore)
    tts: ChatterboxTTS | None = None
    replica_counter: int = 0
    ws_clients: set[WebSocket] = field(default_factory=set)
    # Ссылки на фоновые задачи озвучки: без них asyncio может собрать задачу GC.
    audio_tasks: set[asyncio.Task] = field(default_factory=set)

    async def publish(self, text: str, stimulus: Stimulus) -> None:
        self.replica_counter += 1
        replica_id = self.replica_counter
        marker, clean = parse(text)
        message: dict[str, Any] = {
            "type": "replica",
            "id": replica_id,
            # Зрителю — текст без эмо-маркеров; в синтез уходит оригинал.
            "text": clean if self.settings.text_enabled else "",
            "effect": stimulus.type,
        }
        voice_on = (
            self.settings.voice_enabled
            and self._voice_fresh(stimulus)
            and self.tts is not None
            and self.tts.available
        )
        if voice_on:
            voice = pick_voice(self.settings, stimulus.type, stimulus.priority, marker)
            task = asyncio.get_running_loop().create_task(
                self._send_audio(replica_id, text, voice)
            )
            self.audio_tasks.add(task)
            task.add_done_callback(self.audio_tasks.discard)
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

    def _voice_fresh(self, stimulus: Stimulus) -> bool:
        """Голос уместен, только пока событие свежее tts_max_age_s.

        TTS синтезируется с задержкой (очередь, генерация, сеть); если событие
        успело устареть, озвучивать реакцию поздно — текст всё равно покажем.
        """
        return time.time() - stimulus.created_at <= self.settings.tts_max_age_s

    async def _send_audio(self, replica_id: int, text: str, voice: str) -> None:
        try:
            wav = await asyncio.to_thread(self.tts.synth, text, voice)
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
