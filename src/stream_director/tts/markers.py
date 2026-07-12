"""Эмо-маркеры OpenAudio S1: список для промпта и вырезание из текста оверлея."""

from __future__ import annotations

import re

# Подмножество поддерживаемых моделью маркеров: короткое, чтобы не размывать
# промпт. Ровно этот список попадает в инструкцию LLM (commentary/defaults.py).
EMOTION_MARKERS: tuple[str, ...] = (
    "angry", "excited", "disdainful", "surprised", "confident", "sad",
    "whispering", "shouting", "laughing", "chuckling", "sighing",
)

_MARKER_RE = re.compile(r"\((?:%s)\)\s*" % "|".join(EMOTION_MARKERS))


def strip_markers(text: str) -> str:
    """Убрать известные маркеры: зрителю — чистый текст, движку — с маркерами.

    Незнакомые скобки не трогаем: LLM может законно скобки использовать.
    """
    return _MARKER_RE.sub("", text).strip()
