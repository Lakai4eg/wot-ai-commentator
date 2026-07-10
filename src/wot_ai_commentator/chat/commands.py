"""Парсер чат-команд режиссирования."""

from __future__ import annotations

import re
from dataclasses import dataclass

COMMANDS = {"dir", "roast", "hype", "stats", "mute"}
ADMIN_COMMANDS = {"mute"}

_MUTE_RE = re.compile(r"^(\d+)\s*(s|s\b|m|с|м)?$", re.IGNORECASE)


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


def parse_mute_arg(arg: str | None) -> float | None:
    """«10m»/«10м» → 600, «30s»/«30с» → 30, «5» → 300 (минуты). Иначе None."""
    if not arg:
        return None
    m = _MUTE_RE.match(arg.strip())
    if not m:
        return None
    value = int(m.group(1))
    if value <= 0:
        return None
    unit = (m.group(2) or "m").lower()
    if unit in ("s", "с"):
        return float(value)
    return float(value * 60)
