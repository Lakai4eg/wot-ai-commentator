"""Пул шаблонных реплик: файлы-заготовки, каждый шаблон — один раз за сессию."""

from __future__ import annotations

import logging
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

from ..stimulus import Stimulus

log = logging.getLogger(__name__)


class TemplatePool:
    """Шаблоны из папки: <ключ события>.txt, строка = шаблон, # — комментарий.

    take() выдаёт каждый шаблон один раз за сессию — общий расход для
    затравок, дословного эфира и фолбэков. exhausted_pick() — аварийный
    выбор при мёртвой LLM, когда свежего не осталось: повторы разрешены,
    но не из последних трёх.
    """

    def __init__(self, templates_dir: str | Path,
                 variant_key: Callable[[Stimulus], str]) -> None:
        self._variant_key = variant_key
        self.templates: dict[str, list[str]] = {}
        self._used: set[str] = set()
        self._recent: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=3))
        directory = Path(templates_dir)
        if not directory.is_dir():
            log.warning("папка шаблонов не найдена: %s", directory)
            return
        for file in sorted(directory.glob("*.txt")):
            try:
                # utf-8-sig: Блокнот на Windows дописывает BOM — терпим.
                raw = file.read_text(encoding="utf-8-sig")
            except OSError:
                log.warning("не читается файл шаблонов: %s", file)
                continue
            lines = [line.strip() for line in raw.splitlines()]
            lines = [l for l in lines if l and not l.startswith("#")]
            if lines:
                self.templates[file.stem] = lines
        if not self.templates:
            log.warning("шаблоны не загружены из %s", directory)

    def _options(self, stimulus: Stimulus) -> tuple[str, list[str]] | None:
        """Шаблоны по вариантному ключу; нет такого файла — откат на тип."""
        key = self._variant_key(stimulus)
        options = self.templates.get(key)
        if options is None:
            key, options = stimulus.type, self.templates.get(stimulus.type)
        if options is None:
            return None
        return key, options

    def take(self, stimulus: Stimulus) -> str | None:
        resolved = self._options(stimulus)
        if resolved is None:
            return None
        key, options = resolved
        unused = [o for o in options if o not in self._used]
        if not unused:
            return None
        choice = random.choice(unused)
        self._used.add(choice)
        self._recent[key].append(choice)
        return choice

    def exhausted_pick(self, stimulus: Stimulus) -> str | None:
        resolved = self._options(stimulus)
        if resolved is None:
            return None
        key, options = resolved
        recent = self._recent[key]
        pool = [o for o in options if o not in recent]
        if not pool:  # вариантов мало — хотя бы не повторяем последний
            pool = [o for o in options if not recent or o != recent[-1]] or list(options)
        choice = random.choice(pool)
        recent.append(choice)
        return choice
