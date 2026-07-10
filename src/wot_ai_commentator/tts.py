"""Silero TTS (требует torch + omegaconf) + кольцевое аудио-хранилище."""

from __future__ import annotations

import io
import itertools
import logging
import threading
import wave
from collections import OrderedDict

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000


class SileroTTS:
    """Русский TTS на CPU. Без установленного torch — available=False."""

    def __init__(self, voice: str = "baya"):
        self.voice = voice
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
            log.info("Silero TTS загружен (голос %s)", voice)
        except Exception as e:
            log.warning("Silero TTS недоступен (%s) — голос отключён", e)

    def synth(self, text: str) -> bytes | None:
        if not self.available or self._model is None:
            return None
        try:
            with self._lock:
                audio = self._model.apply_tts(
                    text=text, speaker=self.voice, sample_rate=SAMPLE_RATE
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
