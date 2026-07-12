"""Пофайловый журнал сырых событий Live Client API — по файлу на матч.

Пишем каждое НОВОЕ событие журнала ровно один раз (по EventID), включая
необработанные маппером, — чтобы видеть, что реально шлёт клиент (например,
есть ли событие по личинкам/Voidgrubs, которого нет в документации). Снапшоты
и поллы не логируем: только события журнала — поэтому без спама.

Формат — JSON Lines (по объекту на строку), флаш после каждой записи, чтобы
данные не терялись при аварийном выходе. Новый матч → новый файл; старый файл
при этом закрывается.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, TextIO

from ...paths import LOL_EVENTS_DIR as DEFAULT_DIR

log = logging.getLogger(__name__)


class LolEventLog:
    """Журнал событий одного матча в отдельном файле."""

    def __init__(self, dir_path: Path | str = DEFAULT_DIR,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(dir_path)
        self._clock = clock
        self._file: TextIO | None = None
        self._game_no = 0

    def start_game(self, meta: dict) -> None:
        """Новый матч — новый файл (прежний закрываем), первой строкой — мета."""
        self.close()
        self._game_no += 1
        now = self._clock()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"lol-{stamp}-{self._game_no}.jsonl"
            self._file = path.open("a", encoding="utf-8")
        except OSError:
            log.exception("LolEventLog: не удалось открыть файл матча")
            self._file = None
            return
        self._write({"kind": "game_start", "at": now, **meta})

    def log_event(self, ev: dict) -> None:
        """Одна строка на событие журнала. До start_game — молча пропускаем."""
        if self._file is None:
            return
        self._write({
            "kind": "event",
            "EventID": ev.get("EventID"),
            "EventName": ev.get("EventName"),
            "EventTime": ev.get("EventTime"),
            "raw": ev,
        })

    def _write(self, obj: dict) -> None:
        if self._file is None:
            return
        try:
            self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._file.flush()
        except (OSError, ValueError, TypeError):
            log.exception("LolEventLog: запись не удалась")

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


class NullEventLog:
    """Заглушка: маппер без журнала (тесты, отключённое логирование)."""

    def start_game(self, meta: dict) -> None:  # noqa: D401 - no-op
        pass

    def log_event(self, ev: dict) -> None:  # noqa: D401 - no-op
        pass

    def close(self) -> None:  # noqa: D401 - no-op
        pass
