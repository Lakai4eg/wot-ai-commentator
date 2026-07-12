"""Одно подключение к SQLite на всё приложение: чат-юзеры + промпты."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def script(self, sql: str) -> None:
        with self._lock, self._conn:
            self._conn.executescript(sql)

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Пишущий запрос; возвращает rowcount (или lastrowid для INSERT)."""
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self) -> None:
        self._conn.close()
