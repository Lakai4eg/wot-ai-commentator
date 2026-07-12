"""Промпты в БД: пресеты персон, правки текстов, брифы под технику/чемпиона."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..commentary.defaults import (
    PERSONA_BUILTIN,
    PERSONA_BUILTIN_NAME,
    default_prompt,
)
from .connection import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompts (
    key TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_briefs (
    game TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    text TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""


@dataclass
class Brief:
    game: str
    subject: str   # «Т-100, ЛТ, тир 10» / «Yasuo, мид»
    text: str
    generated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptStore:
    def __init__(self, db: Database):
        self._db = db
        self._db.script(_SCHEMA)
        self._seed_builtin()

    def _seed_builtin(self) -> None:
        """Встроенный пресет персоны заводится один раз, при первом запуске."""
        if self._db.one("SELECT id FROM personas WHERE is_builtin = 1") is None:
            self._db.execute(
                "INSERT INTO personas (name, text, is_builtin, created_at) VALUES (?, ?, 1, ?)",
                (PERSONA_BUILTIN_NAME, PERSONA_BUILTIN, _now()),
            )

    # --- персоны -------------------------------------------------------

    def list_personas(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT id, name, text, is_builtin, created_at FROM personas ORDER BY id"
        )]

    def get_persona(self, persona_id: int) -> dict | None:
        row = self._db.one(
            "SELECT id, name, text, is_builtin, created_at FROM personas WHERE id = ?",
            (persona_id,),
        )
        return dict(row) if row else None

    def active_persona_text(self, persona_id: int) -> str:
        """Текст активной персоны; пресет удалён/не найден — встроенный дефолт."""
        persona = self.get_persona(persona_id)
        if persona:
            return persona["text"]
        builtin = self._db.one("SELECT text FROM personas WHERE is_builtin = 1")
        return builtin["text"] if builtin else PERSONA_BUILTIN

    def create_persona(self, name: str, text: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("имя пресета пустое")
        return self._db.execute(
            "INSERT INTO personas (name, text, is_builtin, created_at) VALUES (?, ?, 0, ?)",
            (name, text, _now()),
        )

    def update_persona(self, persona_id: int, name: str | None = None,
                       text: str | None = None) -> bool:
        persona = self.get_persona(persona_id)
        if persona is None:
            return False
        self._db.execute(
            "UPDATE personas SET name = ?, text = ? WHERE id = ?",
            (name.strip() if name else persona["name"],
             text if text is not None else persona["text"],
             persona_id),
        )
        return True

    def delete_persona(self, persona_id: int) -> bool:
        """Встроенный пресет не удаляется — вернёт False."""
        persona = self.get_persona(persona_id)
        if persona is None or persona["is_builtin"]:
            return False
        return self._db.execute("DELETE FROM personas WHERE id = ?", (persona_id,)) > 0

    def reset_persona(self, persona_id: int) -> bool:
        """Вернуть встроенному пресету заводской текст."""
        persona = self.get_persona(persona_id)
        if persona is None or not persona["is_builtin"]:
            return False
        self._db.execute(
            "UPDATE personas SET name = ?, text = ? WHERE id = ?",
            (PERSONA_BUILTIN_NAME, PERSONA_BUILTIN, persona_id),
        )
        return True

    # --- тексты промптов ------------------------------------------------

    def get_prompt(self, key: str) -> str:
        """Правка пользователя; её нет — заводской текст из кода."""
        row = self._db.one("SELECT text FROM prompts WHERE key = ?", (key,))
        return row["text"] if row else default_prompt(key)

    def is_customized(self, key: str) -> bool:
        return self._db.one("SELECT 1 FROM prompts WHERE key = ?", (key,)) is not None

    def set_prompt(self, key: str, text: str) -> None:
        self._db.execute(
            """INSERT INTO prompts (key, text, updated_at) VALUES (?, ?, ?)
               ON CONFLICT (key) DO UPDATE SET text = excluded.text,
                                               updated_at = excluded.updated_at""",
            (key, text, _now()),
        )

    def reset_prompt(self, key: str) -> None:
        self._db.execute("DELETE FROM prompts WHERE key = ?", (key,))

    # --- брифы ----------------------------------------------------------

    def get_brief(self, game: str) -> Brief | None:
        row = self._db.one(
            "SELECT game, subject, text, generated_at FROM game_briefs WHERE game = ?",
            (game,),
        )
        return Brief(**dict(row)) if row else None

    def save_brief(self, game: str, subject: str, text: str) -> None:
        self._db.execute(
            """INSERT INTO game_briefs (game, subject, text, generated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (game) DO UPDATE SET subject = excluded.subject,
                                                text = excluded.text,
                                                generated_at = excluded.generated_at""",
            (game, subject, text, _now()),
        )

    def clear_brief(self, game: str) -> None:
        self._db.execute("DELETE FROM game_briefs WHERE game = ?", (game,))
