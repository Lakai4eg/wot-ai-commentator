"""Корни файловой раскладки: состояние пользователя и папка установки.

Portable-лаунчер выставляет STREAM_DIRECTOR_HOME (состояние в %LOCALAPPDATA%,
вне папки релиза) и STREAM_DIRECTOR_INSTALL (папка дистрибутива с versions/ и
current.txt). Переменных нет — значит, запуск из репозитория: состояние в
рабочей директории, как было до автообновления, версиями никто не управляет.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

HOME = Path(os.environ.get("STREAM_DIRECTOR_HOME", ".")).resolve()
_install = os.environ.get("STREAM_DIRECTOR_INSTALL")
INSTALL: Path | None = Path(_install).resolve() if _install else None

DATA_DIR = HOME / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "chat-users.db"
VOICES_DIR = DATA_DIR / "voices"
LOL_EVENTS_DIR = DATA_DIR / "lol-events"
MODEL_DIR = HOME / "models" / "s1-mini"
RUNTIME_DIR = HOME / "gpu-runtime"
UPDATE_CACHE_DIR = HOME / "cache" / "update"
UPDATE_STATE_PATH = HOME / "update.json"

# Состояние сборок до автообновления лежало рядом с exe.
_LEGACY_TREES = ("data", "models", "gpu-runtime")


def migrate_state(legacy_root: Path | None = None) -> None:
    """Перенести data/, models/, gpu-runtime/ из папки установки в HOME.

    По дереву за раз и только если в HOME такого ещё нет: апдейтер создаёт HOME
    раньше, чем приложение доберётся сюда, так что судить по факту
    существования HOME нельзя — затёрли бы настройки пользователя.

    Переносим через <дерево>.migrating. Дистрибутив вполне может лежать на D:,
    а HOME быть на C: — тогда move вырождается в копирование, и оборванная
    копия, лежи она сразу в HOME/<дерево>, при следующем запуске сошла бы за
    «уже перенесли»: приложение молча стартовало бы с пустыми настройками, а
    гигабайты моделей поехали бы качаться заново. Готовое дерево въезжает в
    HOME одним переименованием внутри одного тома — атомарно.

    Ошибка переноса не должна валить запуск: оригинал остаётся на месте, пишем
    в лог и пробуем в следующий раз.
    """
    root = legacy_root or INSTALL
    if root is None or root == HOME:
        return
    for name in _LEGACY_TREES:
        src, dst = root / name, HOME / name
        if not src.is_dir() or dst.exists():
            continue
        staging = HOME / f"{name}.migrating"
        log.info("Переношу %s → %s", src, dst)
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            # Хвост оборвавшегося переноса: копировать поверх нельзя.
            shutil.rmtree(staging, ignore_errors=True)
            shutil.move(str(src), str(staging))
            staging.rename(dst)
        except OSError as e:
            log.error("не удалось перенести %s: %s — оставляю в %s", name, e, src)
            shutil.rmtree(staging, ignore_errors=True)
