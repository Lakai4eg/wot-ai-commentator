"""Автообновление portable-сборки: спросить, скачать, переключить версию.

Запускается лаунчером ДО приложения, отдельным процессом. Сервер не поднимает,
состояние пользователя (data/, models/, gpu-runtime/ в %LOCALAPPDATA%) не
трогает — потому модели и не перекачиваются при обновлении.

Ничего не перезаписывается на месте: новая версия распаковывается в соседнюю
папку versions/<версия>/, а переключение — одна атомарная запись current.txt.
Работающий python.exe при этом никто не трогает, блокировок файлов не возникает.

Коды возврата:
    0  — запускай текущую версию (нет обновления, отказ, любая ошибка);
    10 — версия переключена, перечитай current.txt.

Обновление не имеет права мешать запуску: всё, что может сломаться, ловится и
сводится к коду 0. Единственное окно с ошибкой — если пользователь явно нажал
«Да»: молчать, когда человек ждёт результата, невежливо.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import httpx

from . import __version__, paths
from .update_check import GITHUB_LATEST_URL, fetch_update

EXIT_RUN_CURRENT = 0
EXIT_SWITCHED = 10

# Подставная точка релизов для сквозной проверки сценариев без GitHub.
RELEASE_URL = os.environ.get("STREAM_DIRECTOR_UPDATE_URL", GITHUB_LATEST_URL)

TITLE = "Stream Director — обновление"
MB_YESNOCANCEL = 0x03
MB_ICONQUESTION = 0x20
MB_ICONERROR = 0x10
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
IDCANCEL, IDYES = 2, 6


def ask(version: str, current: str) -> int:
    """Диалог «обновиться?». Esc и крестик неотличимы от «Отмены» — версия
    будет пропущена. Это не тупик: баннер о новой версии в панели показывается
    независимо от skipped_version, а о следующем релизе спросят снова.
    """
    text = (
        f"Доступна версия {version}, у вас {current}.\n\n"
        "Да — скачать и обновить.\n"
        f"Нет — запустить {current}, напомнить в следующий раз.\n"
        f"Отмена — больше не предлагать {version}."
    )
    return ctypes.windll.user32.MessageBoxW(
        None, text, TITLE,
        MB_YESNOCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST,
    )


def error_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        None, text, TITLE, MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
    )


def load_state() -> dict:
    try:
        return json.loads(paths.UPDATE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    paths.UPDATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.UPDATE_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def download(url: str, dest: Path) -> None:
    """Скачать с докачкой по Range: обрыв связи не заставляет качать заново.

    416 означает, что докачивать нечего — .part уже полного размера (обрыв
    случился между последним чанком и переименованием). Без этой ветки такой
    .part навсегда отравил бы обновление на эту версию: каждая попытка
    упиралась бы в 416, а кэш чистится только на успешном пути.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.is_file() else 0
    # Второй заход всегда идёт с offset = 0, то есть без Range и без 416.
    while True:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
        ) as r:
            if r.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE and offset:
                part.unlink(missing_ok=True)
                offset = 0
                continue
            if r.status_code == 200:  # сервер не умеет Range — начинаем заново
                offset = 0
            r.raise_for_status()
            total = offset + int(r.headers.get("Content-Length", 0))
            with part.open("ab" if offset else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    offset += len(chunk)
                    pct = f"{offset * 100 // total}%" if total else "..."
                    print(f"\rDownloading update: {pct} "
                          f"({offset // 2**20}/{total // 2**20} MB)", end="", flush=True)
        print()
        part.replace(dest)
        return


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _swap_launcher(install_dir: Path, version_dir: Path) -> None:
    """Работающий exe нельзя перезаписать, но можно переименовать.

    Новый лаунчер сначала кладём рядом целиком и только потом меняем местами
    двумя переименованиями: между ними exe отсутствует лишь на микросекунды.
    Копируй мы прямо на место старого, обрыв в этот момент оставил бы папку
    установки вообще без exe — запускать было бы нечего.

    Старый лаунчер продолжает работать как ни в чём не бывало; .old удаляется
    при следующем старте. Сборка с --skip-launcher exe не содержит — тогда
    менять нечего.
    """
    new = version_dir / "StreamDirector.exe"
    cur = install_dir / "StreamDirector.exe"
    if not new.is_file():
        return
    if cur.is_file() and cur.read_bytes() == new.read_bytes():
        return
    incoming = install_dir / "StreamDirector.exe.new"
    old = install_dir / "StreamDirector.exe.old"
    incoming.unlink(missing_ok=True)
    old.unlink(missing_ok=True)
    shutil.copy2(new, incoming)
    if cur.is_file():
        cur.rename(old)
    incoming.rename(cur)


def install(zip_path: Path, version: str, install_dir: Path) -> None:
    """Распаковать versions/<version>/ из архива и переключить current.txt.

    Zip релиза — это целиком папка свежей установки, поэтому берём из него
    только поддерево нужной версии: current.txt в корне архива нам не указ,
    его мы пишем сами.

    Запись current.txt — точка невозврата, поэтому она идёт ПОСЛЕДНЕЙ, после
    распаковки и подмены exe. Упади что-то раньше — указатель по-прежнему
    смотрит на рабочую версию, и всё, что мы теряем, это зря скачанный архив.
    Порядок наоборот означал бы кирпич: лаунчер, получив от нас код 0, запустил
    бы старую версию, а та своим mark_known_good() снесла бы папку, на которую
    уже указывает current.txt.
    """
    versions = install_dir / "versions"
    target = versions / version
    staging = versions / f"{version}.tmp"
    for leftover in (staging, target):
        if leftover.exists():
            shutil.rmtree(leftover)
    prefix = f"StreamDirector/versions/{version}/"
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist()
                   if n.startswith(prefix) and not n.endswith("/")]
        if not members:
            raise RuntimeError(f"в архиве нет {prefix}")
        for name in members:
            dest = staging / name[len(prefix):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    staging.rename(target)
    _swap_launcher(install_dir, target)
    _write_atomic(install_dir / "current.txt", version)


def main() -> int:
    paths.migrate_state()
    install_dir = paths.INSTALL
    if install_dir is None:
        return EXIT_RUN_CURRENT  # запуск не из дистрибутива — обновлять нечего
    try:
        info = asyncio.run(fetch_update(__version__, url=RELEASE_URL))
    except Exception:
        info = None
    if info is None:
        return EXIT_RUN_CURRENT
    if not info["zip_url"] or not info["sha_url"]:
        print(f"release {info['version']} has no expected assets, skipping")
        return EXIT_RUN_CURRENT
    state = load_state()
    if state.get("skipped_version") == info["version"]:
        return EXIT_RUN_CURRENT

    choice = ask(info["version"], __version__)
    if choice == IDCANCEL:
        state["skipped_version"] = info["version"]
        save_state(state)
        return EXIT_RUN_CURRENT
    if choice != IDYES:
        return EXIT_RUN_CURRENT

    zip_path = paths.UPDATE_CACHE_DIR / f"StreamDirector-v{info['version']}-win64.zip"
    try:
        download(info["zip_url"], zip_path)
        expected = httpx.get(
            info["sha_url"], follow_redirects=True, timeout=30.0
        ).text.split()[0].strip()
        if sha256(zip_path) != expected:
            zip_path.unlink(missing_ok=True)
            raise RuntimeError("контрольная сумма архива не совпала — файл повреждён")
        install(zip_path, info["version"], install_dir)
    except Exception as e:
        error_box(
            f"Не удалось обновиться до {info['version']}:\n{e}\n\n"
            f"Запускаю текущую версию {__version__}."
        )
        return EXIT_RUN_CURRENT
    shutil.rmtree(paths.UPDATE_CACHE_DIR, ignore_errors=True)
    print(f"updated to {info['version']}")
    return EXIT_SWITCHED


if __name__ == "__main__":
    sys.exit(main())
