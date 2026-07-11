"""Сборка portable-дистрибутива Windows: «скачал → распаковал → работает».

Всё в комплекте: embedded CPython + зависимости (CPU-torch), исходники
проекта, собранный фронтенд, модель Silero, лаунчер. Спека:
docs/superpowers/specs/2026-07-11-portable-windows-build-design.md.

Запуск из корня репо на Windows (Python 3.12+, Node 18+, MSVC cl.exe):
    python scripts/build_portable.py [--skip-launcher]
Результат: build/StreamDirector-v<версия>-win64.zip
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
STAGE = BUILD / "StreamDirector"
CACHE = BUILD / "cache"

PYTHON_EMBED_URL = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
)
PYTHON_EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
SILERO_MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SILERO_MODEL_SHA256 = "896ab96347d5bd781ab97959d4fd6885620e5aab52405d3445626eb7c1414b00"

# ._pth управляет sys.path embedded-питона; PYTHONPATH при нём игнорируется,
# поэтому путь к исходникам приложения прописан здесь, а не в лаунчере.
PTH_CONTENT = "python312.zip\n.\nLib\\site-packages\n..\\app\\src\nimport site\n"


def read_version() -> str:
    init = (ROOT / "src" / "stream_director" / "__init__.py").read_text(
        encoding="utf-8"
    )
    return re.search(r'__version__ = "([^"]+)"', init).group(1)


def download(url: str, dest: Path, sha256: str) -> Path:
    """Скачать с проверкой SHA256; кэш в build/cache переживает пересборки."""
    if not sha256:
        sys.exit(f"SHA256 для {url} не заполнен в build_portable.py")
    if not dest.exists():
        print(f"скачиваю {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
    actual = hashlib.sha256(dest.read_bytes()).hexdigest()
    if actual != sha256:
        dest.unlink()
        sys.exit(f"SHA256 не совпал для {dest.name}: {actual}")
    return dest


def build_python() -> None:
    print("== embedded python + зависимости")
    archive = download(PYTHON_EMBED_URL, CACHE / "python-embed.zip", PYTHON_EMBED_SHA256)
    pydir = STAGE / "python"
    with zipfile.ZipFile(archive) as z:
        z.extractall(pydir)
    (pydir / "python312._pth").write_text(PTH_CONTENT, encoding="ascii")
    site = pydir / "Lib" / "site-packages"
    site.mkdir(parents=True)
    # Зависимости ставим питоном сборочной машины: та же ОС/арх — колёса
    # совместимы. На Windows колёса torch с PyPI — CPU-only, CUDA не приедет.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(site), ".[ml]"],
        check=True,
        cwd=ROOT,
    )
    # Сам проект едет исходниками в app/src — из site-packages его убираем,
    # чтобы не было двух копий кода.
    for leftover in site.glob("stream_director*"):
        shutil.rmtree(leftover)


def build_web() -> None:
    print("== фронтенд")
    npm = shutil.which("npm") or "npm"
    subprocess.run([npm, "ci"], check=True, cwd=ROOT / "web")
    subprocess.run([npm, "run", "build"], check=True, cwd=ROOT / "web")


def copy_app() -> None:
    print("== исходники приложения")
    shutil.copytree(
        ROOT / "src" / "stream_director",
        STAGE / "app" / "src" / "stream_director",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(ROOT / "web" / "dist", STAGE / "app" / "web" / "dist")


def fetch_model() -> None:
    print("== модель Silero")
    archive = download(SILERO_MODEL_URL, CACHE / "silero_v4_ru.pt", SILERO_MODEL_SHA256)
    (STAGE / "models").mkdir()
    shutil.copy2(archive, STAGE / "models" / "silero_v4_ru.pt")


def build_launcher() -> None:
    print("== лаунчер")
    subprocess.run(
        [
            "cl", "/nologo", "/W4", "/O1",
            str(ROOT / "scripts" / "launcher.c"),
            f"/Fe:{STAGE / 'StreamDirector.exe'}",
            f"/Fo:{BUILD / 'launcher.obj'}",
        ],
        check=True,
    )


def make_zip(version: str) -> Path:
    print("== zip")
    out = BUILD / f"StreamDirector-v{version}-win64"
    # Не with_suffix: точки в версии ломают его ("v0.1.0-win64" → "v0.1.zip").
    return Path(
        shutil.make_archive(str(out), "zip", root_dir=BUILD, base_dir="StreamDirector")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-launcher", action="store_true",
        help="не компилировать лаунчер (нет MSVC; CI всегда компилирует)",
    )
    args = parser.parse_args()

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    version = read_version()
    build_python()
    build_web()
    copy_app()
    fetch_model()
    if not args.skip_launcher:
        build_launcher()
    out = make_zip(version)
    print(f"готово: {out} ({out.stat().st_size / 1e6:.0f} МБ)")


if __name__ == "__main__":
    main()
