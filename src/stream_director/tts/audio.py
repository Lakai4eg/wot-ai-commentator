"""Кольцо последних WAV для отдачи оверлею по id."""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict


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
