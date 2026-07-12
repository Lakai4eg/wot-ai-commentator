"""Референсы голосов: data/voices/<имя>.wav + <имя>.txt (транскрипт)."""

from __future__ import annotations

import re
from pathlib import Path

VOICES_DIR = Path("data") / "voices"
# Голос без референса — собственный тембр модели.
DEFAULT_VOICE = "default"
_NAME_RE = re.compile(r"^[a-zA-Zа-яА-ЯёЁ0-9_-]{1,32}$")


def list_voices() -> list[str]:
    names = []
    if VOICES_DIR.is_dir():
        for wav in VOICES_DIR.glob("*.wav"):
            if wav.with_suffix(".txt").is_file():
                names.append(wav.stem)
    return [DEFAULT_VOICE, *sorted(names)]


def save_voice(name: str, wav: bytes, transcript: str) -> None:
    if not _NAME_RE.match(name) or name == DEFAULT_VOICE:
        raise ValueError("имя: буквы/цифры/дефис/подчёркивание, до 32 символов")
    if not transcript.strip():
        raise ValueError("нужен текст-транскрипт референса")
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    (VOICES_DIR / f"{name}.wav").write_bytes(wav)
    (VOICES_DIR / f"{name}.txt").write_text(transcript.strip(), encoding="utf-8")


def delete_voice(name: str) -> bool:
    paths = voice_paths(name)
    if paths is None:
        return False
    for p in paths:
        p.unlink(missing_ok=True)
    return True


def voice_paths(name: str) -> tuple[Path, Path] | None:
    """(wav, txt) существующего референса; default и незнакомые имена — None."""
    if name == DEFAULT_VOICE or not _NAME_RE.match(name):
        return None
    wav, txt = VOICES_DIR / f"{name}.wav", VOICES_DIR / f"{name}.txt"
    return (wav, txt) if wav.is_file() and txt.is_file() else None


def pick_voice(settings, stim_type: str, priority) -> str:
    """Голос под контекст: override по типу > правило по приоритету > дефолт.

    Имя без файлов референса игнорируется на своём уровне — синтез не падает.
    """
    known = set(list_voices())
    for candidate in (
        settings.voice_overrides.get(stim_type),
        settings.voice_by_priority.get(priority.name.lower()),
        settings.default_voice,
    ):
        if candidate in known:
            return candidate
    return DEFAULT_VOICE
