"""Эмо-маркеры: список для промпта LLM и превращение маркера в стиль синтеза.

Chatterbox не понимает маркеры текстом — маркер снимается из реплики и
превращается в пару (exaggeration, cfg_weight); зрителю уходит чистый текст.
"""

from __future__ import annotations

import re

# Список, попадающий в инструкцию LLM (commentary/defaults.py): четыре
# контрастные подачи — промежуточные градации на слух неразличимы (прослушивание
# спайка), больше маркеров = размытый промпт без выигрыша в звуке.
EMOTION_MARKERS: tuple[str, ...] = ("angry", "excited", "sad", "whispering")

# маркер → (exaggeration, cfg_weight): четыре угла пространства стилей.
DEFAULT_STYLE: tuple[float, float] = (0.5, 0.5)
MARKER_STYLE: dict[str, tuple[float, float]] = {
    "angry": (0.9, 0.25),
    "excited": (0.75, 0.35),
    "sad": (0.35, 0.65),
    "whispering": (0.25, 0.7),
}

# Маркеры старого (широкого) списка: новым персонам их не предлагают, но
# сохранённые в БД промпты могут ставить — вырезаем и ведём к ближайшему стилю,
# иначе движок зачитает маркер вслух.
_LEGACY: dict[str, str] = {
    "shouting": "angry",
    "disdainful": "angry",
    "laughing": "excited",
    "chuckling": "excited",
    "confident": "excited",
    "surprised": "excited",
    "sighing": "sad",
}

_MARKER_RE = re.compile(r"\((%s)\)\s*" % "|".join((*MARKER_STYLE, *_LEGACY)))


def parse(text: str) -> tuple[str | None, str]:
    """Первый известный маркер + текст, очищенный от ВСЕХ известных маркеров.

    LLM просят ставить один маркер в начале, но полагаться на это нельзя.
    Легаси-маркеры приводятся к канонному имени. Незнакомые скобки не трогаем:
    LLM может законно их использовать.
    """
    m = _MARKER_RE.search(text)
    marker = _LEGACY.get(m.group(1), m.group(1)) if m else None
    return marker, _MARKER_RE.sub("", text).strip()
