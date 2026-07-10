"""Парсер чат-команд режиссирования. Единственная команда — !dir <текст>."""

from __future__ import annotations

from dataclasses import dataclass

COMMANDS = {"dir"}


@dataclass
class Command:
    name: str
    arg: str | None = None


def parse_command(text: str) -> Command | None:
    text = (text or "").strip()
    if not text.startswith("!"):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return None
    name = parts[0].lower()
    if name not in COMMANDS:
        return None
    arg = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    if name == "dir" and not arg:
        return None
    return Command(name=name, arg=arg)
