# Portable Windows Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Один zip на GitHub Releases: пользователь распаковывает, запускает `StreamDirector.exe`, панель открывается сама — без установки Python/Node/зависимостей, голос работает офлайн.

**Architecture:** Embedded CPython 3.12 + зависимости, поставленные pip'ом в CI; проект едет папкой исходников (окружение идентично dev); модель Silero и собранный фронтенд в комплекте; маленький C-лаунчер. GitHub Actions на тег `v*` собирает, смоук-тестит распакованный артефакт и публикует релиз. Спека: `docs/superpowers/specs/2026-07-11-portable-windows-build-design.md`.

**Tech Stack:** Python 3.12 (embeddable), FastAPI/uvicorn, httpx, torch CPU + Silero v4_ru, Vite/React, MSVC (`cl.exe`), GitHub Actions (windows-latest).

## Global Constraints

- **НЕ КОММИТИТЬ** — правило пользователя («never commit changes») важнее шаблона плана: все изменения остаются в рабочем дереве, шагов с `git commit` в плане нет.
- Платформа: только Windows x64. Embedded Python пиннед: 3.12.10 (`python-3.12.10-embed-amd64.zip`).
- Репозиторий GitHub: `Lakai4eg/wot-ai-commentator`. Порт сервера: `8710`.
- Файл модели: `models/silero_v4_ru.pt` (относительно CWD приложения). Имя артефакта: `StreamDirector-v<версия>-win64.zip`.
- Env var автооткрытия панели: `STREAM_DIRECTOR_OPEN_PANEL` (значение `"1"`).
- Новых Python-зависимостей не добавлять (httpx уже в `dependencies`).
- Комментарии, докстринги и пользовательские тексты — на русском (конвенция репо). Исключение: сообщения лаунчера в консоль — ASCII-английский (кодовая страница консоли Windows портит кириллицу).
- Тесты гонять из корня репо: `python -m pytest` (весь прогон должен быть зелёным в конце каждой задачи).
- Все изменения обратно совместимы с dev-запуском `python -m stream_director` (браузер сам не открывается, модель грузится из torch.hub).

---

### Task 1: Модуль проверки обновлений `update_check.py`

**Files:**
- Create: `src/stream_director/update_check.py`
- Test: `tests/test_update_check.py`

**Interfaces:**
- Consumes: `httpx` (уже в зависимостях); `stream_director.__version__` не трогает — версию передают параметром.
- Produces (Task 2 использует именно эти имена):
  - `GITHUB_LATEST_URL: str`
  - `is_newer(latest: str, current: str) -> bool`
  - `fetch_update(current: str, url: str = GITHUB_LATEST_URL, transport: httpx.BaseTransport | None = None) -> dict | None` — возвращает `{"version": "X.Y.Z", "url": "<html_url>"}` или `None`
  - `apply_update_status(statuses: dict, current: str, transport: httpx.BaseTransport | None = None) -> None` (async) — кладёт результат в `statuses["update_available"]`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_update_check.py`:

```python
"""Проверка обновлений: сравнение версий и запрос к GitHub Releases."""

import httpx

from stream_director.update_check import apply_update_status, fetch_update, is_newer


def test_is_newer_basic():
    assert is_newer("0.2.0", "0.1.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.0.9", "0.1.0")


def test_is_newer_v_prefix():
    assert is_newer("v0.2.0", "0.1.0")
    assert not is_newer("v0.1.0", "0.1.0")


def test_is_newer_garbage_is_false():
    # Кривой тег с GitHub не должен ронять приложение — просто «не новее».
    assert not is_newer("beta", "0.1.0")
    assert not is_newer("", "0.1.0")
    assert not is_newer("1.2.x", "0.1.0")


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_fetch_update_newer_version():
    def handler(request):
        assert "api.github.com" in str(request.url)
        return httpx.Response(
            200, json={"tag_name": "v9.9.9", "html_url": "https://example.com/rel"}
        )

    info = await fetch_update("0.1.0", transport=_transport(handler))
    assert info == {"version": "9.9.9", "url": "https://example.com/rel"}


async def test_fetch_update_same_version_returns_none():
    def handler(request):
        return httpx.Response(200, json={"tag_name": "v0.1.0", "html_url": "x"})

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_fetch_update_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom")

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_fetch_update_http_error_returns_none():
    def handler(request):
        return httpx.Response(403, json={"message": "rate limit"})

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_apply_update_status_sets_key():
    def handler(request):
        return httpx.Response(200, json={"tag_name": "v9.9.9", "html_url": "u"})

    statuses: dict = {}
    await apply_update_status(statuses, "0.1.0", transport=_transport(handler))
    assert statuses["update_available"] == {"version": "9.9.9", "url": "u"}


async def test_apply_update_status_no_update_no_key():
    def handler(request):
        raise httpx.ConnectError("offline")

    statuses: dict = {}
    await apply_update_status(statuses, "0.1.0", transport=_transport(handler))
    assert "update_available" not in statuses
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_update_check.py -v`
Expected: FAIL/ERROR с `ModuleNotFoundError: No module named 'stream_director.update_check'`

- [ ] **Step 3: Реализация**

Создать `src/stream_director/update_check.py`:

```python
"""Проверка обновлений: GitHub Releases → баннер «доступна версия» в панели.

Любая ошибка (нет сети, rate limit, кривой JSON) молча гасится — проверка
обновлений не имеет права мешать работе приложения.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

GITHUB_LATEST_URL = (
    "https://api.github.com/repos/Lakai4eg/wot-ai-commentator/releases/latest"
)


def is_newer(latest: str, current: str) -> bool:
    """Числовое сравнение версий вида X.Y.Z (допустим префикс v).

    Непарсибельные строки — False: кривой тег не повод для баннера.
    """

    def parse(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.strip().lstrip("v").split("."))

    try:
        return parse(latest) > parse(current)
    except ValueError:
        return False


async def fetch_update(
    current: str,
    url: str = GITHUB_LATEST_URL,
    transport: httpx.BaseTransport | None = None,
) -> dict | None:
    """Свежайший релиз новее current → {"version", "url"}, иначе None."""
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            data = r.json()
        tag = str(data.get("tag_name", ""))
        if is_newer(tag, current):
            return {"version": tag.lstrip("v"), "url": str(data.get("html_url", ""))}
    except Exception:
        log.debug("проверка обновлений не удалась", exc_info=True)
    return None


async def apply_update_status(
    statuses: dict,
    current: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Одноразовая фоновая проверка: нашли новее — кладём в статусы панели."""
    info = await fetch_update(current, transport=transport)
    if info is not None:
        statuses["update_available"] = info
```

- [ ] **Step 4: Тесты зелёные**

Run: `python -m pytest tests/test_update_check.py -v`
Expected: 8 passed

- [ ] **Step 5: Полный прогон**

Run: `python -m pytest`
Expected: все тесты passed

---

### Task 2: Версия в `/api/status` + фоновая проверка обновлений при старте

**Files:**
- Modify: `src/stream_director/server.py` (эндпоинт `status`, ~строка 143)
- Modify: `src/stream_director/main.py` (функция `run()`, список `tasks`, ~строка 150)
- Test: `tests/test_server.py` (добавить в конец)

**Interfaces:**
- Consumes: `apply_update_status(statuses, current)` из Task 1; `stream_director.__version__` (уже существует в `src/stream_director/__init__.py`, значение `"0.1.0"`).
- Produces: `/api/status` отдаёт `"app_version": "<__version__>"` всегда и `"update_available": {"version", "url"}` — когда фоновая проверка нашла новее (через `ctx.statuses`, которые эндпоинт уже разворачивает `**ctx.statuses`). Эти поля читают Task 5 (фронтенд) и Task 8 (смоук).

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_server.py`:

```python
@pytest.mark.asyncio
async def test_status_reports_app_version(client):
    from stream_director import __version__

    r = await client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["app_version"] == __version__
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/test_server.py::test_status_reports_app_version -v`
Expected: FAIL с `KeyError: 'app_version'`

- [ ] **Step 3: Реализация**

В `src/stream_director/server.py`:

К импортам из пакета (после строки `from .broadcast import OverlayBroadcaster`) добавить:

```python
from . import __version__
```

В эндпоинте `status()` добавить поле в возвращаемый словарь (после строки `"active_game": active,`):

```python
            "app_version": __version__,
```

В `src/stream_director/main.py`:

К импортам добавить:

```python
from . import __version__
from .update_check import apply_update_status
```

В `run()` в список `tasks` (после `asyncio.create_task(supervised("lol", lol.source.run)),`) добавить одну задачу — без `supervised`, потому что `apply_update_status` одноразовая и сама глушит все ошибки:

```python
        asyncio.create_task(apply_update_status(ctx.statuses, __version__)),
```

- [ ] **Step 4: Тест зелёный**

Run: `python -m pytest tests/test_server.py -v`
Expected: все тесты test_server passed, включая `test_status_reports_app_version`

- [ ] **Step 5: Полный прогон**

Run: `python -m pytest`
Expected: все тесты passed

---

### Task 3: Локальная модель Silero в `tts.py`

**Files:**
- Modify: `src/stream_director/tts.py` (константы вверху + `SileroTTS.__init__`, строки 17–60)
- Test: `tests/test_tts.py` (добавить в конец)

**Interfaces:**
- Consumes: ничего из других задач.
- Produces: `LOCAL_MODEL_PATH = Path("models") / "silero_v4_ru.pt"` (относительно CWD — лаунчер из Task 6 ставит CWD в корень дистрибутива) и `resolve_model_source(local_path: Path = LOCAL_MODEL_PATH) -> Path | None`. Task 7 кладёт файл модели ровно в `models/silero_v4_ru.pt`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_tts.py`:

```python
def test_resolve_model_source_local_file(tmp_path):
    from stream_director.tts import resolve_model_source

    model = tmp_path / "silero_v4_ru.pt"
    model.write_bytes(b"fake")
    assert resolve_model_source(model) == model


def test_resolve_model_source_missing_file(tmp_path):
    from stream_director.tts import resolve_model_source

    assert resolve_model_source(tmp_path / "nope.pt") is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_tts.py -v`
Expected: FAIL с `ImportError: cannot import name 'resolve_model_source'`

- [ ] **Step 3: Реализация**

В `src/stream_director/tts.py`:

К импортам добавить (после `from collections import OrderedDict`):

```python
from pathlib import Path
```

После строки `DEFAULT_VOICE = "baya"` добавить:

```python
# Portable-сборка кладёт модель сюда (относительно CWD — его ставит лаунчер).
LOCAL_MODEL_PATH = Path("models") / "silero_v4_ru.pt"


def resolve_model_source(local_path: Path = LOCAL_MODEL_PATH) -> Path | None:
    """Локальная модель (portable-сборка), если файл есть; иначе None → torch.hub."""
    return local_path if local_path.is_file() else None
```

В `SileroTTS.__init__` заменить блок загрузки модели (строки от `import torch` до `model.to(torch.device("cpu"))` включительно) на:

```python
            import torch  # noqa: PLC0415

            local = resolve_model_source()
            if local is not None:
                # Офлайн-загрузка: silero-модель — это torch.package-архив.
                from torch import package  # noqa: PLC0415

                importer = package.PackageImporter(str(local))
                model = importer.load_pickle("tts_models", "model")
                log.info("Silero TTS: локальная модель %s", local)
            else:
                model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-models",
                    model="silero_tts",
                    language="ru",
                    speaker="v4_ru",
                    trust_repo=True,
                )
            model.to(torch.device("cpu"))
```

Остальное в `__init__` (присваивание `self._model`, `self.available`, лог, `except`) не меняется.

- [ ] **Step 4: Тесты зелёные**

Run: `python -m pytest tests/test_tts.py -v`
Expected: все тесты test_tts passed

- [ ] **Step 5: Полный прогон**

Run: `python -m pytest`
Expected: все тесты passed. Ветка загрузки через `torch.package` покрывается смоук-тестом CI (Task 8) — в юнитах torch может отсутствовать.

---

### Task 4: Автооткрытие панели в браузере

**Files:**
- Modify: `src/stream_director/main.py` (новая функция + wiring в `run()`)
- Test: `tests/test_main.py` (добавить в конец)

**Interfaces:**
- Consumes: ничего из других задач.
- Produces: `open_panel_when_ready(server, url: str, opener: Callable[[str], object] | None = None) -> None` (async) в `stream_director.main`; активация — env var `STREAM_DIRECTOR_OPEN_PANEL == "1"` (её выставляет лаунчер из Task 6). У `server` читается только атрибут `started: bool` (утиный тип — в тесте фейк).

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_main.py`:

```python
class _FakeServer:
    def __init__(self):
        self.started = False


@pytest.mark.asyncio
async def test_open_panel_waits_for_server_start():
    from stream_director.main import open_panel_when_ready

    server = _FakeServer()
    opened: list[str] = []
    task = asyncio.create_task(
        open_panel_when_ready(server, "http://x/panel", opener=opened.append)
    )
    await asyncio.sleep(0.05)
    assert opened == []  # сервер ещё не поднялся — браузер не трогаем

    server.started = True
    await asyncio.wait_for(task, timeout=2.0)
    assert opened == ["http://x/panel"]


@pytest.mark.asyncio
async def test_open_panel_opens_immediately_if_started():
    from stream_director.main import open_panel_when_ready

    server = _FakeServer()
    server.started = True
    opened: list[str] = []
    await asyncio.wait_for(
        open_panel_when_ready(server, "http://x/panel", opener=opened.append),
        timeout=2.0,
    )
    assert opened == ["http://x/panel"]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL с `ImportError: cannot import name 'open_panel_when_ready'`

- [ ] **Step 3: Реализация**

В `src/stream_director/main.py`:

К стандартным импортам добавить:

```python
import os
import webbrowser
```

После функции `supervised(...)` добавить:

```python
async def open_panel_when_ready(
    server, url: str, opener: Callable[[str], object] | None = None
) -> None:
    """Открыть панель, когда сервер реально принимает соединения.

    Лаунчер portable-сборки выставляет STREAM_DIRECTOR_OPEN_PANEL=1 — браузер
    открывает не он, а мы: так на медленном первом старте пользователь не
    увидит «connection refused».
    """
    open_url = opener or webbrowser.open
    while not server.started:
        await asyncio.sleep(0.2)
    open_url(url)
```

В `run()` в список `tasks` (рядом с задачей из Task 2) добавить условную задачу:

```python
    if os.environ.get("STREAM_DIRECTOR_OPEN_PANEL") == "1":
        tasks.append(
            asyncio.create_task(
                open_panel_when_ready(
                    server, f"http://127.0.0.1:{settings.server_port}/panel"
                )
            )
        )
```

Вставить после создания списка `tasks`, до `await server.serve()`.

- [ ] **Step 4: Тесты зелёные**

Run: `python -m pytest tests/test_main.py -v`
Expected: все тесты test_main passed

- [ ] **Step 5: Полный прогон**

Run: `python -m pytest`
Expected: все тесты passed

---

### Task 5: Баннер обновления в панели (фронтенд)

**Files:**
- Modify: `web/src/shared/api.ts` (интерфейс `StatusDto`)
- Modify: `web/src/panel/Panel.tsx` (после строки `{message && <div className="message">{message}</div>}`)

**Interfaces:**
- Consumes: поля `app_version` и `update_available: {version, url}` из `/api/status` (Task 2).
- Produces: ничего для других задач. Тестов на фронте в проекте нет — верификация сборкой.

- [ ] **Step 1: Типы в `api.ts`**

В интерфейс `StatusDto` (после поля `memory?: string[];`) добавить:

```ts
  app_version?: string;
  update_available?: { version: string; url: string };
```

- [ ] **Step 2: Баннер в `Panel.tsx`**

Сразу после строки `{message && <div className="message">{message}</div>}` добавить:

```tsx
      {status.update_available && (
        <div className="message">
          Доступна версия {status.update_available.version} —{" "}
          <a href={status.update_available.url} target="_blank" rel="noreferrer">
            скачать на GitHub
          </a>
        </div>
      )}
```

Используется существующий css-класс `message` — новых стилей не нужно.

- [ ] **Step 3: Верификация сборкой**

Run: `cd web && npm run build`
Expected: сборка без ошибок TypeScript (`tsc` входит в build-скрипт vite-проекта; если нет — дополнительно `npx tsc --noEmit` должен пройти чисто)

---

### Task 6: Лаунчер `scripts/launcher.c`

**Files:**
- Create: `scripts/launcher.c`

**Interfaces:**
- Consumes: раскладку дистрибутива из Task 7: рядом с exe лежат `python\python.exe` и `app\src` (пути приложения в `sys.path` прописывает `python312._pth`, а НЕ `PYTHONPATH` — embedded Python с `._pth` игнорирует `PYTHONPATH`).
- Produces: `StreamDirector.exe`, который: 1) ставит CWD в свою директорию; 2) выставляет `STREAM_DIRECTOR_OPEN_PANEL=1`; 3) запускает `python\python.exe -m stream_director`; 4) при ненулевом коде выхода ждёт Enter. Компилирует его Task 7 (`build_launcher()`).

- [ ] **Step 1: Написать лаунчер**

Создать `scripts/launcher.c`:

```c
/* Stream Director — portable-лаунчер.
 * Запускает python\python.exe -m stream_director из своей директории.
 * Сообщения в консоль — ASCII-английский: кодовая страница консоли на
 * свежей Windows портит кириллицу. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

static void wait_for_enter(void) {
    fwprintf(stderr, L"Press Enter to exit...");
    getwchar();
}

int main(void) {
    wchar_t dir[MAX_PATH];
    if (GetModuleFileNameW(NULL, dir, MAX_PATH) == 0) {
        return 1;
    }
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash != NULL) {
        *slash = L'\0';
    }
    SetCurrentDirectoryW(dir);

    /* Питон, увидев эту переменную, откроет панель в браузере, когда сервер
     * реально поднимется. sys.path дистрибутива задаёт python312._pth. */
    _wputenv_s(L"STREAM_DIRECTOR_OPEN_PANEL", L"1");

    wchar_t cmd[] = L"python\\python.exe -m stream_director";
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"Failed to start python\\python.exe (error %lu).\n",
                 GetLastError());
        fwprintf(stderr, L"Make sure the zip was fully extracted.\n");
        wait_for_enter();
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (code != 0) {
        fwprintf(stderr, L"\nStream Director exited with error code %lu.\n", code);
        fwprintf(stderr, L"(Is another copy already running on port 8710?)\n");
        wait_for_enter();
    }
    return (int)code;
}
```

- [ ] **Step 2: Компиляция (если есть MSVC)**

Run: `where cl` — если `cl.exe` не найден, шаг пропустить: компиляцию проверит CI (Task 8), где `ilammy/msvc-dev-cmd` даёт MSVC.

Если найден: `cl /nologo /W4 /O1 scripts\launcher.c /Fe:build\StreamDirector.exe /Fo:build\launcher.obj`
Expected: компиляция без warnings, появился `build\StreamDirector.exe`

---

### Task 7: Скрипт сборки `scripts/build_portable.py`

**Files:**
- Create: `scripts/build_portable.py`
- Modify: `.gitignore` (добавить строку `build/`, если её нет)

**Interfaces:**
- Consumes: `scripts/launcher.c` (Task 6); `src/stream_director/__init__.py::__version__`; раскладку из спеки.
- Produces: `build/StreamDirector-v<версия>-win64.zip` с деревом `StreamDirector/{StreamDirector.exe, python/, app/src/stream_director/, app/web/dist/, models/silero_v4_ru.pt}`; флаг `--skip-launcher` для машин без MSVC. Task 8 запускает этот скрипт в CI и смоук-тестит распакованный результат.

- [ ] **Step 1: Написать скрипт**

Создать `scripts/build_portable.py`:

```python
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
PYTHON_EMBED_SHA256 = ""  # заполняется один раз, см. план (Task 7, Step 2)
SILERO_MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SILERO_MODEL_SHA256 = ""  # заполняется один раз, см. план (Task 7, Step 2)

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
    shutil.make_archive(str(out), "zip", root_dir=BUILD, base_dir="StreamDirector")
    return out.with_suffix(".zip")


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
```

- [ ] **Step 2: Заполнить SHA256-пины**

Run (скачивает оба файла, ~1–2 ГБ суммарно, и печатает хэши):

```powershell
python -c "import hashlib,urllib.request;`nfor u in ('https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip','https://models.silero.ai/models/tts/ru/v4_ru.pt'):`n    print(hashlib.sha256(urllib.request.urlopen(u).read()).hexdigest(), u)"
```

(если многострочник неудобен — выполнить то же самое двумя отдельными `python -c` по одному URL)

Вписать напечатанные хэши в константы `PYTHON_EMBED_SHA256` и `SILERO_MODEL_SHA256` в `scripts/build_portable.py`.

- [ ] **Step 3: `.gitignore`**

Добавить в `.gitignore` строку `build/` (если её ещё нет).

- [ ] **Step 4: Локальная верификация сборки**

Run: `python scripts/build_portable.py --skip-launcher`
Expected: скрипт отрабатывает без ошибок, в конце печатает `готово: ...StreamDirector-v0.1.0-win64.zip (<размер> МБ)`; внутри zip — дерево `StreamDirector/python/...`, `StreamDirector/app/src/stream_director/...`, `StreamDirector/app/web/dist/...`, `StreamDirector/models/silero_v4_ru.pt`.

Проверка дерева: `python -c "import zipfile; names=zipfile.ZipFile('build/StreamDirector-v0.1.0-win64.zip').namelist(); print([n for n in names if n.count('/')<=2][:30])"`

---

### Task 8: Смоук-тест и CI-релиз

**Files:**
- Create: `scripts/smoke_test.py`
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: zip из Task 7; поле `app_version` и `tts_status` из `/api/status` (Task 2 и существующий статус TTS из `main.py`: `"loading" | "ready" | "unavailable"`).
- Produces: `python scripts/smoke_test.py <распакованная-папка> <версия> [--timeout N] [--skip-launcher-check]` — код возврата 0/1; workflow `release` на тег `v*`, публикующий GitHub Release с zip.

- [ ] **Step 1: Написать смоук-тест**

Создать `scripts/smoke_test.py`:

```python
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

    proc = subprocess.Popen(
        [str(args.dist / "python" / "python.exe"), "-m", "stream_director"],
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
                if tts == "ready":
                    print("OK: сервер отвечает, версия верна, TTS готов")
                    return 0
                if tts == "unavailable":
                    print("FAIL: TTS unavailable — torch/модель не работают в сборке")
                    return 1
            time.sleep(3)
        print("FAIL: таймаут ожидания tts_status=ready")
        return 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Написать workflow**

Создать `.github/workflows/release.yml`:

```yaml
name: release

on:
  push:
    tags: ["v*"]

jobs:
  release:
    runs-on: windows-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      # cl.exe в PATH — для компиляции лаунчера
      - uses: ilammy/msvc-dev-cmd@v1

      - name: Тесты
        run: |
          python -m pip install -e ".[dev]"
          python -m pytest

      - name: Версия совпадает с тегом
        shell: python
        run: |
          import os, re
          tag = os.environ["GITHUB_REF_NAME"].removeprefix("v")
          init = open("src/stream_director/__init__.py", encoding="utf-8").read()
          code = re.search(r'__version__ = "([^"]+)"', init).group(1)
          toml = open("pyproject.toml", encoding="utf-8").read()
          proj = re.search(r'^version = "([^"]+)"', toml, re.M).group(1)
          assert tag == code == proj, f"тег {tag}, __version__ {code}, pyproject {proj}"

      - name: Сборка portable
        run: python scripts/build_portable.py

      - name: Смоук-тест
        run: |
          $zip = Get-ChildItem build/StreamDirector-*.zip
          Expand-Archive $zip.FullName -DestinationPath smoke
          $version = $env:GITHUB_REF_NAME.TrimStart("v")
          python scripts/smoke_test.py smoke/StreamDirector $version

      - name: Релиз
        uses: softprops/action-gh-release@v2
        with:
          files: build/StreamDirector-*.zip
          generate_release_notes: true
```

- [ ] **Step 3: Локальная верификация смоука**

Распаковать zip из Task 7 и прогнать смоук против него:

```powershell
Expand-Archive build/StreamDirector-v0.1.0-win64.zip -DestinationPath build/smoke -Force
python scripts/smoke_test.py build/smoke/StreamDirector 0.1.0 --skip-launcher-check
```

Expected: `OK: сервер отвечает, версия верна, TTS готов` и код возврата 0. Это же подтверждает Task 3 (локальная модель реально грузится через `torch.package` — в логе процесса строка `Silero TTS: локальная модель models\silero_v4_ru.pt`).

- [ ] **Step 4: Валидность YAML**

Run: `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text(encoding='utf-8')); print('ok')"`
(если PyYAML не установлен — `python -m pip install pyyaml` во временное окружение или пропустить: CI сам провалится на кривом YAML)
Expected: `ok`

---

### Task 9: README — установка из релиза

**Files:**
- Modify: `README.md` (раздел «Первый запуск (с Gemini)», строки 9–41)

**Interfaces:**
- Consumes: имя артефакта и URL релизов (`https://github.com/Lakai4eg/wot-ai-commentator/releases/latest`).
- Produces: ничего для других задач.

- [ ] **Step 1: Переписать раздел установки**

Заменить раздел `## Первый запуск (с Gemini)` (от заголовка до строки перед `![Панель управления]`) на:

```markdown
## Установка (Windows)

1. Скачай `StreamDirector-vX.Y.Z-win64.zip` из
   [последнего релиза](https://github.com/Lakai4eg/wot-ai-commentator/releases/latest)
   и распакуй в любую папку. Python и Node.js ставить не нужно — всё в комплекте,
   включая голос.
2. Запусти `StreamDirector.exe` — откроется консоль с логами, а панель сама
   откроется в браузере. При первом запуске SmartScreen может предупредить о
   неизвестном издателе: «Подробнее» → «Выполнить в любом случае».
3. **Мод** (только для WoT): скачай `wotstat.data-provider_<версия>.mtmod` из
   [релизов](https://github.com/wotstat/wotstat-data-provider/releases) и положи в
   `<папка игры>/mods/<версия игры>/`. Перезапусти игру.
4. **Ключ Gemini**: бесплатно в [Google AI Studio](https://aistudio.google.com/apikey)
   (из РФ нужен маршрут до `generativelanguage.googleapis.com` — VPN/pbr).
5. В панели вставь API-ключ Gemini (провайдер «Gemini» выбран по умолчанию),
   укажи канал Twitch. После сохранения ключа панель сама проверит LLM пробным
   запросом.
6. **OBS**: добавь http://127.0.0.1:8710/overlay как Browser Source на весь холст.

Готово: бейджи `чат`, `LLM` и `голос` в шапке панели зелёные, а `WoT`/`LoL`
загорится, как только запустится игра (активная отмечена ●) — иди в бой,
реплики пойдут сами. О новых версиях панель сообщит баннером со ссылкой.
```

- [ ] **Step 2: Установка из исходников — в «Разработку»**

В раздел `## Разработка` перед существующим блоком команд добавить:

```markdown
Установка из исходников (Mac/Linux или без готового билда):

1. Python 3.12+ и Node.js 18+ (Windows: `winget install Python.Python.3.12 OpenJS.NodeJS.LTS`,
   в установщике Python — галочка «Add python.exe to PATH»).
2. `python -m pip install -e .[dev,ml]` (ml = голос: torch + Silero)
   и `cd web && npm install && npm run build && cd ..`.
3. Запуск: `python -m stream_director`, панель — http://127.0.0.1:8710/panel.

Сборка portable-дистрибутива: `python scripts/build_portable.py`
(Windows, нужен MSVC; `--skip-launcher` — без него). Релиз собирает CI
на тег `v*` (`.github/workflows/release.yml`).
```

- [ ] **Step 3: Верификация**

Перечитать README целиком: нумерация сквозная, ссылки валидные, упоминаний «установи Python/Node» в разделе установки не осталось.

---

## Self-Review (выполнено при написании плана)

- **Покрытие спеки:** локальная модель — Task 3; автооткрытие — Task 4; версия — Task 2 (уже была в `__init__.py`); проверка обновлений — Tasks 1, 2, 5; лаунчер — Task 6; скрипт сборки (embedded python, `._pth`, CPU-torch, модель, zip) — Task 7; CI+смоук — Task 8; README — Task 9. Вне объёма спеки (подпись кода, автообновление, macOS/Linux) — не планируется.
- **Готча `._pth` vs `PYTHONPATH`:** учтена — путь `..\app\src` прописан в `python312._pth` (Task 7), лаунчер `PYTHONPATH` не трогает (Task 6).
- **Типы согласованы:** `fetch_update`/`apply_update_status` (Task 1) ↔ wiring в `main.py` (Task 2); `update_available: {version, url}` ↔ `StatusDto` (Task 5); `app_version`/`tts_status` ↔ смоук (Task 8); `models/silero_v4_ru.pt` ↔ `LOCAL_MODEL_PATH` (Tasks 3, 7).
- **Без плейсхолдеров:** SHA256-константы намеренно пустые с точной инструкцией заполнения (Task 7 Step 2) и жёстким отказом скрипта при пустом пине.
