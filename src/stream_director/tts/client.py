"""Клиент голосового worker-а Chatterbox: жизненный цикл подпроцесса и HTTP-вызовы."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from . import bootstrap
from .bootstrap import CREATE_NO_WINDOW, MODEL_DIR, RUNTIME_DIR, BootstrapError
from .markers import DEFAULT_STYLE, MARKER_STYLE, parse
from .voices import VOICES_DIR

log = logging.getLogger(__name__)

WORKER_PATH = Path(__file__).parent / "worker.py"
# Модель разворачивается в VRAM до пары минут на медленных дисках.
READY_TIMEOUT_S = 240.0
SYNTH_TIMEOUT_S = 90.0
MAX_RESTARTS = 3


class ChatterboxTTS:
    """Голос приложения. Недоступен — реплики идут текстом, это штатно."""

    def __init__(self, on_status: Callable[[dict], None]):
        self._status_cb = on_status
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._ready = False
        self._starting = threading.Lock()
        self._restarts = 0
        self._http = httpx.Client(timeout=SYNTH_TIMEOUT_S)
        self._set("checking")

    @property
    def available(self) -> bool:
        return self._ready

    def _set(self, state: str, progress: dict | None = None, error: str | None = None) -> None:
        self._status_cb({"state": state, "progress": progress, "error": error})

    def start(self) -> None:
        """Bootstrap + спавн. Идемпотентен: параллельный вызов просто выходит."""
        if not self._starting.acquire(blocking=False):
            return
        try:
            self._ready = False
            self._restarts = 0
            reason = bootstrap.check_gpu()
            if reason is not None:
                self._set("no_gpu", error=reason)
                return
            bootstrap.ensure_runtime(self._status_cb)
            bootstrap.ensure_weights(self._status_cb)
            self._spawn_and_wait()
        except BootstrapError as e:
            self._set("error", error=str(e))
        except Exception as e:
            log.exception("Старт голоса упал")
            self._set("error", error=f"не удалось запустить голос: {e}")
        finally:
            self._starting.release()

    def _spawn_and_wait(self) -> None:
        self._set("starting")
        self._kill_proc()
        with socket.socket() as s:  # свободный порт: без гонок в пределах локалхоста
            s.bind(("127.0.0.1", 0))
            self._port = s.getsockname()[1]
        # RUAccent тянет данные с HF Hub, если workdir неполон; веса зеркалим
        # сами (ruaccent-data.zip), поэтому запрещаем worker-у ходить в сеть.
        env = {**os.environ, "HF_HUB_OFFLINE": "1"}
        self._proc = subprocess.Popen(
            [sys.executable, str(WORKER_PATH),
             "--runtime", str(RUNTIME_DIR), "--model-dir", str(MODEL_DIR),
             "--voices-dir", str(VOICES_DIR), "--port", str(self._port)],
            stdin=subprocess.PIPE,  # worker умирает по EOF — страховка от сирот
            creationflags=CREATE_NO_WINDOW, env=env,
        )
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._set("error", error=f"голосовой движок завершился (код {self._proc.returncode})")
                return
            try:
                phase = self._http.get(self._url("/health"), timeout=2.0).json()["phase"]
            except (httpx.HTTPError, KeyError, ValueError):
                time.sleep(0.5)
                continue
            if phase == "ready":
                self._ready = True
                self._set("ready")
                return
            self._set("loading")
            time.sleep(0.5)
        self._set("error", error="голосовой движок не поднялся за отведённое время")

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._port}{path}"

    def synth(self, text: str, voice: str | None = None) -> bytes | None:
        if not self._ready:
            return None
        # Маркер разбирается здесь, а не в broadcast: /api/tts/preview зовёт
        # synth напрямую, и маркеры в превью должны работать так же, как в эфире.
        marker, clean = parse(text)
        exaggeration, cfg_weight = MARKER_STYLE.get(marker, DEFAULT_STYLE)
        try:
            r = self._http.post(self._url("/synth"), json={
                "text": clean, "voice": voice,
                "exaggeration": exaggeration, "cfg_weight": cfg_weight,
            })
            if r.status_code != 200:
                log.warning("synth %s: %s", r.status_code, r.text[:200])
                return None
            return r.content
        except httpx.HTTPError:
            log.warning("Голосовой worker не отвечает — перезапуск в фоне")
            self._ready = False
            threading.Thread(target=self._restart, daemon=True).start()
            return None

    def _restart(self) -> None:
        if self._restarts >= MAX_RESTARTS:
            self._set("error", error="голос падает раз за разом — нажмите «повторить» в панели")
            return
        self._restarts += 1
        time.sleep(5.0 * self._restarts)  # бэкофф как у supervised()
        if self._starting.acquire(blocking=False):
            try:
                self._spawn_and_wait()
            finally:
                self._starting.release()

    def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.close()  # EOF: worker выходит сам
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    def stop(self) -> None:
        self._ready = False
        self._kill_proc()
        self._http.close()
