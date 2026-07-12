"""Белый список пользователей чата, которым доступны команды."""

from __future__ import annotations

from datetime import datetime, timezone

from .connection import Database

# director/admin — доступ к командам; banned — команды запрещены всегда,
# даже в открытом режиме (commands_open_to_all).
ROLES = ("director", "admin", "banned")

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


class ChatUserDB:
    def __init__(self, db: Database):
        self._db = db
        self._db.script(_SCHEMA)

    def add_user(self, username: str, role: str = "director", platform: str = "twitch") -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
        username = username.strip().lower()
        if not username:
            raise ValueError("username is empty")
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO chat_users (platform, username, role, added_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (platform, username) DO UPDATE SET role = excluded.role""",
            (platform, username, role, now),
        )

    def remove_user(self, username: str, platform: str = "twitch") -> bool:
        return self._db.execute(
            "DELETE FROM chat_users WHERE platform = ? AND username = ?",
            (platform, username.strip().lower()),
        ) > 0

    def get_role(self, username: str, platform: str = "twitch") -> str | None:
        row = self._db.one(
            "SELECT role FROM chat_users WHERE platform = ? AND username = ?",
            (platform, username.strip().lower()),
        )
        return row["role"] if row else None

    def list_users(self) -> list[dict]:
        rows = self._db.query(
            "SELECT id, platform, username, role, added_at FROM chat_users ORDER BY username"
        )
        return [dict(r) for r in rows]
