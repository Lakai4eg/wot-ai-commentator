"""Silero TTS (требует torch + omegaconf) + кольцевое аудио-хранилище."""

from __future__ import annotations

import io
import itertools
import logging
import threading
import wave
from collections import OrderedDict

from .config import Settings
from .stimulus import Priority

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
VOICES: tuple[str, ...] = ("aidar", "baya", "kseniya", "xenia", "eugene", "random")
DEFAULT_VOICE = "baya"


def pick_voice(settings: Settings, stim_type: str, priority: Priority) -> str:
    """Голос под контекст: override по типу > правило по приоритету > дефолт.

    Любое имя вне VOICES игнорируется на своём уровне — синтез не падает.
    """
    for candidate in (
        settings.voice_overrides.get(stim_type),
        settings.voice_by_priority.get(priority.name.lower()),
        settings.default_voice,
    ):
        if candidate in VOICES:
            return candidate
    return DEFAULT_VOICE


class SileroTTS:
    """Русский TTS на CPU. Без установленного torch — available=False."""

    def __init__(self, voice: str = "baya"):
        self.voice = voice if voice in VOICES else "baya"
        self.available = False
        self._model = None
        self._lock = threading.Lock()
        try:
            import torch  # noqa: PLC0415

            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language="ru",
                speaker="v4_ru",
                trust_repo=True,
            )
            model.to(torch.device("cpu"))
            self._model = model
            self.available = True
            log.info("Silero TTS загружен (голос по умолчанию %s)", self.voice)
        except Exception as e:
            log.warning("Silero TTS недоступен (%s) — голос отключён", e)

    def synth(self, text: str, voice: str | None = None) -> bytes | None:
        if not self.available or self._model is None:
            return None
        speaker = voice if voice in VOICES else self.voice
        try:
            with self._lock:
                audio = self._model.apply_tts(
                    text=text, speaker=speaker, sample_rate=SAMPLE_RATE
                )
            pcm = (audio.numpy() * 32767).astype("int16").tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
            return buf.getvalue()
        except Exception:
            log.exception("TTS synth failed")
            return None


class AudioStore:
    """Кольцо последних WAV для отдачи оверлею по id."""

    def __init__(self, capacity: int = 16):
        self.capacity = capacity
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def put(self, wav: bytes) -> str:
        audio_id = str(next(self._counter))
        with self._lock:
            self._items[audio_id] = wav
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return audio_id

    def get(self, audio_id: str) -> bytes | None:
        with self._lock:
            return self._items.get(audio_id)
