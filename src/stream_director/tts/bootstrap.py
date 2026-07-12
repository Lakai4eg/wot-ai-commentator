"""Первый старт: проверка GPU, установка gpu-runtime, докачка весов S1-mini.

Всё синхронное — вызывается из потока executor, event loop не трогает.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import httpx

from .pins import RUNTIME_PACKAGES, TORCH_INDEX_URL, WEIGHTS, WEIGHTS_BASE_URL

log = logging.getLogger(__name__)

RUNTIME_DIR = Path("gpu-runtime")
MODEL_DIR = Path("models") / "s1-mini"
# Windows: не показывать консольное окно дочернего pip.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

StatusCb = Callable[[dict], None]


class BootstrapError(Exception):
    """Человекочитаемая причина, почему голос не поднялся."""


def _st(state: str, progress: dict | None = None, error: str | None = None) -> dict:
    return {"state": state, "progress": progress, "error": error}


def check_gpu() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "nvidia-smi не найден — для голоса нужна видеокарта NVIDIA"
    if out.returncode != 0 or "GPU" not in out.stdout:
        return "NVIDIA GPU не обнаружен — голос работать не будет"
    return None


def _pins_fingerprint() -> str:
    raw = json.dumps([TORCH_INDEX_URL, RUNTIME_PACKAGES], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def ensure_runtime(status: StatusCb) -> None:
    marker = RUNTIME_DIR / ".complete"
    if marker.is_file() and marker.read_text() == _pins_fingerprint():
        return
    # Пины сменились — старый рантайм несовместим, ставим с нуля.
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    status(_st("downloading_runtime", {"step": "подготовка…"}))
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(RUNTIME_DIR),
        "--index-url", TORCH_INDEX_URL,
        "--extra-index-url", "https://pypi.org/simple",
        "--progress-bar", "off",
        *RUNTIME_PACKAGES,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    tail: list[str] = []
    for line in proc.stdout:
        tail.append(line.rstrip())
        tail = tail[-30:]
        m = re.match(r"\s*(Collecting|Downloading)\s+(\S+)", line)
        if m:
            status(_st("downloading_runtime", {"step": m.group(2)}))
    if proc.wait() != 0:
        raise BootstrapError("pip не смог установить GPU-рантайм:\n" + "\n".join(tail[-8:]))
    # fish_speech.models.dac.inference на импорте зовёт pyrootutils.setup_root
    # с indicator=".project-root" — ищет этот файл вверх по дереву. pip его не
    # ставит, без него импорт падает FileNotFoundError и worker не поднимается.
    # Пустого файла в корне рантайма достаточно (см. spike-results.md).
    (RUNTIME_DIR / ".project-root").touch()
    marker.write_text(_pins_fingerprint())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_weights(status: StatusCb) -> None:
    if any(not sha for sha, _ in WEIGHTS.values()):
        raise BootstrapError("зеркало весов не опубликовано (pins.py пуст) — соберите релиз моделей")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    total_mb = sum(size for _, size in WEIGHTS.values()) // 2**20
    done = 0
    for name, (sha, size) in WEIGHTS.items():
        dest = MODEL_DIR / name
        ok_marker = MODEL_DIR / f"{name}.ok"
        if dest.is_file() and ok_marker.is_file():
            done += size
            continue
        _download(f"{WEIGHTS_BASE_URL}/{name}", dest, done, total_mb, status)
        if _sha256(dest) != sha:
            dest.unlink()
            raise BootstrapError(f"SHA256 не совпал для {name} — файл повреждён, перезапустите")
        ok_marker.write_text("ok")
        done += size


def _download(url: str, dest: Path, done_before: int, total_mb: int, status: StatusCb) -> None:
    """Докачка с Range: обрыв сети не заставляет качать гигабайты заново."""
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    offset = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                          timeout=httpx.Timeout(30.0, read=120.0)) as r:
            if r.status_code == 200:  # сервер не умеет Range — начинаем заново
                offset = 0
            elif r.status_code != 206 and offset:
                raise BootstrapError(f"зеркало ответило {r.status_code} на {url}")
            r.raise_for_status()
            mode = "ab" if offset else "wb"
            with tmp.open(mode) as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    offset += len(chunk)
                    status(_st("downloading_model", {
                        "done_mb": (done_before + offset) // 2**20,
                        "total_mb": total_mb,
                    }))
    except httpx.HTTPError as e:
        raise BootstrapError(f"обрыв скачивания {dest.name}: {e}") from e
    tmp.rename(dest)
