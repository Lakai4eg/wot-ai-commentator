"""Смоук portable-сборки: сервер стартует, версия верна, TTS доходит до ready.

Запускает python напрямую (не лаунчер): без STREAM_DIRECTOR_OPEN_PANEL браузер
в CI не открывается; сам лаунчер проверяется по наличию файла.

    python scripts/smoke_test.py <папка StreamDirector> <версия> [--timeout N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATUS_URL = "http://127.0.0.1:8710/api/status"


def poll_status() -> dict | None:
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="распакованная папка StreamDirector")
    parser.add_argument("version", help="ожидаемая версия, напр. 0.1.0")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="секунд на старт + загрузку TTS")
    parser.add_argument("--skip-launcher-check", action="store_true",
                        help="сборка была с --skip-launcher")
    args = parser.parse_args()

    if not args.skip_launcher_check and not (args.dist / "StreamDirector.exe").is_file():
        print("FAIL: в дистрибутиве нет StreamDirector.exe")
        return 1

    current = (args.dist / "current.txt").read_text(encoding="utf-8").strip()
    if current != args.version:
        print(f"FAIL: current.txt = {current!r} != {args.version!r}")
        return 1

    python = args.dist / "versions" / args.version / "python" / "python.exe"
    proc = subprocess.Popen(
        [str(python), "-m", "stream_director"],
        cwd=args.dist,
    )
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(f"FAIL: процесс завершился с кодом {proc.returncode}")
                return 1
            status = poll_status()
            if status is not None:
                if status.get("app_version") != args.version:
                    print(f"FAIL: версия {status.get('app_version')!r} != {args.version!r}")
                    return 1
                tts = status.get("tts_status")
                if tts in ("ready", "no_gpu"):
                    # no_gpu — норма для CI-раннера: сборка живая, голос требует NVIDIA.
                    print(f"OK: сервер отвечает, версия верна, tts={tts}")
                    return 0
                if tts in ("error", "unavailable"):
                    print(f"FAIL: tts_status={tts} — движок не работает в сборке")
                    return 1
            time.sleep(3)
        print("FAIL: таймаут ожидания tts_status=ready")
        return 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
