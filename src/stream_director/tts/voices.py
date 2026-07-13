"""Референсы голосов: data/voices/<имя>.wav (один WAV, без транскрипта)."""

from __future__ import annotations

import re

from ..paths import VOICES_DIR

# Голос без референса — собственный тембр модели.
DEFAULT_VOICE = "default"
_NAME_RE = re.compile(r"^[a-zA-Zа-яА-ЯёЁ0-9_-]{1,32}$")


def list_voices() -> list[str]:
    names = []
    if VOICES_DIR.is_dir():
        names = [wav.stem for wav in VOICES_DIR.glob("*.wav")]
    return [DEFAULT_VOICE, *sorted(names)]


def save_voice(name: str, wav: bytes) -> None:
    if not _NAME_RE.match(name) or name == DEFAULT_VOICE:
        raise ValueError("имя: буквы/цифры/дефис/подчёркивание, до 32 символов")
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    (VOICES_DIR / f"{name}.wav").write_bytes(wav)


def delete_voice(name: str) -> bool:
    if name == DEFAULT_VOICE or not _NAME_RE.match(name):
        return False
    wav = VOICES_DIR / f"{name}.wav"
    if not wav.is_file():
        return False
    wav.unlink()
    # Транскрипты эпохи S1-mini: больше не читаются, но не должны сиротеть.
    (VOICES_DIR / f"{name}.txt").unlink(missing_ok=True)
    return True


def pick_voice(settings, stim_type: str, priority, marker: str | None = None) -> str:
    """Голос под контекст: маркер > override по типу > приоритет > дефолт."""
    known = set(list_voices())
    for candidate in (
        settings.voice_by_marker.get(marker) if marker else None,
        settings.voice_overrides.get(stim_type),
        settings.voice_by_priority.get(priority.name.lower()),
        settings.default_voice,
    ):
        if candidate in known:
            return candidate
    return DEFAULT_VOICE
