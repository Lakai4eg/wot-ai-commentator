"""Мини-БД (SQLite): белый список пользователей чата, которым доступны команды."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("director", "admin")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (platform, username)
);
"""


class WhitelistDB:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def add_user(self, username: str, role: str = "director", platform: str = "twitch") -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
        username = username.strip().lower()
        if not username:
            raise ValueError("username is empty")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO chat_users (platform, username, role, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (platform, username) DO UPDATE SET role = excluded.role""",
                (platform, username, role, now),
            )

    def remove_user(self, username: str, platform: str = "twitch") -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM chat_users WHERE platform = ? AND username = ?",
                (platform, username.strip().lower()),
            )
        return cur.rowcount > 0

    def get_role(self, username: str, platform: str = "twitch") -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT role FROM chat_users WHERE platform = ? AND username = ?",
                (platform, username.strip().lower()),
            ).fetchone()
        return row["role"] if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, platform, username, role, added_at FROM chat_users ORDER BY username"
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
