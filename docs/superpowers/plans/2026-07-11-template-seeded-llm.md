# Шаблоны как затравка для LLM (LoL) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Шаблонные реплики LoL становятся расходуемым пулом: затравка для LLM (`seed`), дословный эфир (`verbatim`) или прежний фолбэк (`off`), каждый шаблон — один раз за сессию.

**Architecture:** Новый класс `TemplatePool` в ядре загружает `.txt`-файлы из `games/lol/templates/` и раздаёт неиспользованные шаблоны; `GameModule` получает опциональное поле `template_pool`; `director.py` ветвится по `Settings.template_mode`; `build_prompt` принимает `seed_line`; панель получает селектор режима.

**Tech Stack:** Python 3.12 (dataclasses, pathlib, pytest + pytest-asyncio), FastAPI/pydantic, React + TypeScript (vite).

**Spec:** `docs/superpowers/specs/2026-07-11-template-seeded-llm-design.md`

## Global Constraints

- **НИКАКИХ git commit** — глобальное правило пользователя «never commit changes». Шаги коммитов в этом плане отсутствуют намеренно; не добавлять их.
- Все комментарии в коде — на русском, в стиле окружающего кода (комментируется «почему», не «что»).
- Значения режима: строго `"seed" | "verbatim" | "off"`, дефолт `"seed"`.
- Кодировка шаблонов при чтении: `utf-8-sig` (терпим BOM от Блокнота).
- Тесты запускать через `rtk`: `rtk cargo`-стиль не нужен, команда — `rtk test python -m pytest <путь> -v` либо просто `python -m pytest <путь> -v` из корня репозитория.
- WoT-модуль не трогаем (его `fallback_line` остаётся прежним).

## Карта файлов

| Файл | Действие | Ответственность |
|---|---|---|
| `src/stream_director/games/template_pool.py` | создать | класс `TemplatePool`: загрузка файлов, `take`, `exhausted_pick` |
| `src/stream_director/games/lol/templates/*.txt` | создать (18 файлов) | данные шаблонов, перенос из `_TEMPLATES` |
| `src/stream_director/games/lol/flavor.py` | изменить | удалить `_TEMPLATES`/`_pick`/`fallback_line`, `_variant_key` → `variant_key` |
| `src/stream_director/games/lol/module.py` | изменить | создание пула, `template_pool=` и новый `fallback_line` |
| `src/stream_director/games/base.py` | изменить | поле `GameModule.template_pool` |
| `src/stream_director/commentary/prompts.py` | изменить | параметр `seed_line` |
| `src/stream_director/director.py` | изменить | ветвление режимов |
| `src/stream_director/config.py` | изменить | `Settings.template_mode` + валидация при загрузке |
| `src/stream_director/server.py` | изменить | `SettingsIn.template_mode` + валидация 400 |
| `pyproject.toml` | изменить | package-data для `templates/*.txt` |
| `web/src/shared/api.ts` | изменить | поле в `SettingsDto` |
| `web/src/panel/Panel.tsx` | изменить | селектор режима в секции «Режиссёр» |
| `tests/test_template_pool.py` | создать | юнит-тесты пула |
| `tests/test_lol_flavor.py` | изменить | переезд проверок шаблонов на пул |
| `tests/test_prompts.py` | изменить | тесты `seed_line` |
| `tests/test_config.py` | изменить | тест валидации `template_mode` |
| `tests/test_server.py` | изменить | тест 400 на кривое значение |
| `tests/test_director.py` | изменить | тесты трёх режимов |

---

### Task 1: TemplatePool

**Files:**
- Create: `src/stream_director/games/template_pool.py`
- Test: `tests/test_template_pool.py`

**Interfaces:**
- Consumes: `stream_director.stimulus.Stimulus` (поля `type: str`, `payload: dict`).
- Produces (для Task 2 и Task 5):
  - `TemplatePool(templates_dir: str | Path, variant_key: Callable[[Stimulus], str])`
  - `TemplatePool.templates: dict[str, list[str]]` — загруженные шаблоны (публичный, читается тестами и диагностикой)
  - `TemplatePool.take(stimulus: Stimulus) -> str | None` — неиспользованный шаблон или `None`
  - `TemplatePool.exhausted_pick(stimulus: Stimulus) -> str | None` — повторный выбор с антиповтором последних 3

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_template_pool.py`:

```python
from stream_director.games.template_pool import TemplatePool
from stream_director.stimulus import Stimulus


def stim(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol", payload=payload)


def key_by_type(stimulus):
    return stimulus.type


def make_pool(tmp_path, files, variant_key=key_by_type):
    for name, content in files.items():
        (tmp_path / f"{name}.txt").write_text(content, encoding="utf-8")
    return TemplatePool(tmp_path, variant_key)


def test_loads_lines_skips_comments_and_blank(tmp_path):
    pool = make_pool(tmp_path, {"frag": "один\n\n# коммент\nдва\n"})
    assert pool.templates == {"frag": ["один", "два"]}


def test_survives_notepad_bom(tmp_path):
    # ﻿ — BOM, который Блокнот Windows дописывает при сохранении.
    (tmp_path / "frag.txt").write_bytes("﻿строка".encode("utf-8"))
    pool = TemplatePool(tmp_path, key_by_type)
    assert pool.templates == {"frag": ["строка"]}


def test_take_unique_until_exhausted(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\nb\nc\n"})
    got = {pool.take(stim("frag")) for _ in range(3)}
    assert got == {"a", "b", "c"}
    assert pool.take(stim("frag")) is None


def test_take_falls_back_to_base_type_key(tmp_path):
    # Файла objective_ours нет — берём общий objective (как старый фолбэк).
    def variant(s):
        return f"objective_{s.payload['side']}"
    pool = make_pool(tmp_path, {"objective": "общий\n"}, variant)
    assert pool.take(stim("objective", side="ours")) == "общий"


def test_variant_key_does_not_leak_to_base_when_file_exists(tmp_path):
    # Файл objective_ours есть, но исчерпан — на общий НЕ откатываемся:
    # исчерпание значит «дальше без шаблонов», а не «берём чужие».
    def variant(s):
        return f"objective_{s.payload['side']}"
    pool = make_pool(tmp_path, {"objective": "общий\n", "objective_ours": "наш\n"}, variant)
    assert pool.take(stim("objective", side="ours")) == "наш"
    assert pool.take(stim("objective", side="ours")) is None


def test_take_none_for_unknown_event(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\n"})
    assert pool.take(stim("death")) is None


def test_missing_dir_yields_empty_pool(tmp_path):
    pool = TemplatePool(tmp_path / "нет_такой_папки", key_by_type)
    assert pool.templates == {}
    assert pool.take(stim("frag")) is None
    assert pool.exhausted_pick(stim("frag")) is None


def test_exhausted_pick_avoids_recent_three(tmp_path):
    pool = make_pool(tmp_path, {"frag": "a\nb\nc\nd\ne\n"})
    picks = [pool.exhausted_pick(stim("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]


def test_exhausted_pick_single_option_still_returns(tmp_path):
    pool = make_pool(tmp_path, {"frag": "одна\n"})
    assert pool.exhausted_pick(stim("frag")) == "одна"
    assert pool.exhausted_pick(stim("frag")) == "одна"


def test_exhausted_pick_not_immediately_after_same_take(tmp_path):
    # take() тоже пишет в «недавние»: аварийный выбор не повторяет
    # только что прозвучавший шаблон.
    pool = make_pool(tmp_path, {"frag": "a\nb\n"})
    last = [pool.take(stim("frag")) for _ in range(2)][-1]
    assert pool.exhausted_pick(stim("frag")) != last
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_template_pool.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'stream_director.games.template_pool'`

- [ ] **Step 3: Реализовать TemplatePool**

Создать `src/stream_director/games/template_pool.py`:

```python
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
```

- [ ] **Step 4: Прогнать тесты**

Run: `python -m pytest tests/test_template_pool.py -v`
Expected: все PASS

---

### Task 2: Файлы шаблонов + подключение пула к LoL-модулю

**Files:**
- Create: `src/stream_director/games/lol/templates/` (18 файлов `.txt`)
- Modify: `src/stream_director/games/lol/flavor.py`
- Modify: `src/stream_director/games/lol/module.py`
- Modify: `src/stream_director/games/base.py`
- Modify: `pyproject.toml`
- Test: `tests/test_lol_flavor.py`

**Interfaces:**
- Consumes: `TemplatePool` из Task 1 (сигнатуры выше).
- Produces (для Task 5):
  - `GameModule.template_pool: TemplatePool | None = None` — у LoL заполнено, у WoT `None`
  - `flavor.variant_key(stimulus: Stimulus) -> str` (бывший `_variant_key`, переименован в публичный)
  - `fallback_line` LoL-модуля = `pool.take(s) or pool.exhausted_pick(s)`

- [ ] **Step 1: Сгенерировать файлы шаблонов из текущего словаря**

Запустить одноразовый скрипт ИЗ КОРНЯ репозитория (пока `_TEMPLATES` ещё существует):

```python
# scratch-скрипт, запускать так: python -c "<код ниже>" либо из файла в скретчпаде
import sys
sys.path.insert(0, "src")
from pathlib import Path
from stream_director.games.lol import flavor

out = Path("src/stream_director/games/lol/templates")
out.mkdir(exist_ok=True)
for key, lines in flavor._TEMPLATES.items():
    out.joinpath(f"{key}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("создано:", len(list(out.glob("*.txt"))), "файлов")
```

Expected: `создано: 18 файлов` — ключи: `battle_start`, `frag`, `death`, `assist`, `multikill`, `first_blood`, `objective`, `objective_ours`, `objective_theirs`, `turret`, `inhib`, `ace_ours`, `ace_theirs`, `ally_feeding`, `ally_carrying_multikill`, `ally_carrying_lead`, `team_gap_spectator`, `team_gap_behind`.

Проверить: `ls src/stream_director/games/lol/templates/` показывает 18 файлов; `frag.txt` начинается со строки `Минус один! Запомните этот момент, больше такого не будет!`.

- [ ] **Step 2: Обновить тесты test_lol_flavor.py**

В `tests/test_lol_flavor.py`:

Заменить импорт (строка 3):

```python
from stream_director.games.lol.flavor import describe_event, flavor_lines
```

Добавить хелпер после `game(...)`:

```python
def lol_module():
    return build_module(Settings(), submit=lambda s: None)
```

Заменить `test_fallback_covers_all_lol_types` (шаблонов теперь нет в flavor — фолбэк живёт в модуле):

```python
def test_fallback_covers_all_lol_types():
    m = lol_module()
    for t in LOL_TYPES:
        line = m.fallback_line(Stimulus(kind="game_event", type=t))
        assert isinstance(line, str) and line
```

Заменить фолбэк-часть `test_battle_start_carries_gojo_bit` (описание не трогаем):

```python
def test_battle_start_carries_gojo_bit():
    # Интро на старте матча — про прибытие Годжо на Ущелье призывателей.
    desc = describe_event(game("battle_start", champion="Yasuo"))
    assert "Годжо" in desc and "Yasuo" in desc
    # И фолбэк (когда LLM мертва) тоже держит эту шутку — любой из мотивов Годжо.
    motifs = ("Годжо", "сильнейший", "Бесконечность", "Шесть Глаз")
    line = lol_module().fallback_line(Stimulus(kind="game_event", type="battle_start"))
    assert any(w in line for w in motifs)
```

Заменить `test_fallback_covers_new_ally_types`:

```python
def test_fallback_covers_new_ally_types():
    m = lol_module()
    cases = (
        game("ally_feeding", champion="Yasuo", deaths=8),
        game("ally_carrying", champion="Lee Sin", label="трипл-килл", count=3),
        game("ally_carrying", champion="Lee Sin", kills=9, my_kills=2),
        game("team_gap", kind="spectator", team_kills=6),
        game("team_gap", kind="behind", diff=12),
    )
    for stim in cases:
        line = m.fallback_line(stim)
        assert isinstance(line, str) and line
```

Заменить `test_fallback_objective_sides_differ` (свой модуль на каждую сторону, чтобы расход пула не мешал):

```python
def test_fallback_objective_sides_differ():
    m = lol_module()
    ours = {m.fallback_line(game("objective", side="ours")) for _ in range(30)}
    theirs = {m.fallback_line(game("objective", side="theirs")) for _ in range(30)}
    assert ours and theirs and ours.isdisjoint(theirs)
```

Заменить `test_templates_are_rich_and_unique` (читаем пул, не словарь):

```python
def test_templates_are_rich_and_unique():
    templates = lol_module().template_pool.templates
    rich = ("battle_start", "frag", "death", "assist", "multikill", "first_blood",
            "turret", "inhib", "ace_ours", "objective_ours", "objective_theirs")
    for key in rich:
        assert len(templates[key]) >= 8, key
    new = ("ally_feeding", "ally_carrying_multikill", "ally_carrying_lead",
           "team_gap_spectator", "team_gap_behind", "ace_theirs")
    for key in new:
        assert len(templates[key]) >= 5, key
    for key, options in templates.items():
        assert len(set(options)) == len(options), f"дубликаты в {key}"
```

Внимание: в текущем `_TEMPLATES` ключи `assist` (7 строк) и `multikill` (9), `frag` (12), `death` (14), `first_blood` (8) — проверить фактические длины после генерации; если `assist` < 8, порог для него в тесте оставить как в текущем тесте (тест сегодня зелёный — значит, длины уже удовлетворяют старым порогам; НЕ менять пороги, они переносятся как есть).

Заменить `test_fallback_no_repeat_last_three`:

```python
def test_fallback_no_repeat_last_three():
    m = lol_module()
    picks = [m.fallback_line(game("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]
```

Добавить новый тест на исчерпание общего пула за сессию:

```python
def test_fallback_take_phase_never_repeats_within_session():
    # Пока пул жив, шаблоны не повторяются вовсе; после исчерпания
    # реплики продолжаются (exhausted_pick), молчания нет.
    m = lol_module()
    total = len(m.template_pool.templates["frag"])
    picks = [m.fallback_line(game("frag")) for _ in range(total)]
    assert len(set(picks)) == total  # фаза take: все уникальны
    assert m.fallback_line(game("frag"))  # и дальше не молчим
```

- [ ] **Step 3: Убедиться, что новые тесты падают**

Run: `python -m pytest tests/test_lol_flavor.py -v`
Expected: FAIL — `GameModule.__init__() got an unexpected keyword argument 'template_pool'` / `AttributeError: template_pool` (поле ещё не существует).

- [ ] **Step 4: Поле в GameModule**

В `src/stream_director/games/base.py` добавить импорт и поле:

```python
from .template_pool import TemplatePool
```

В dataclass `GameModule` после строки `joke_angles: ...`:

```python
    # Пул шаблонов-заготовок (см. director: seed/verbatim); None — не использует.
    template_pool: TemplatePool | None = None
```

- [ ] **Step 5: Почистить flavor.py и подключить пул в module.py**

В `src/stream_director/games/lol/flavor.py`:
1. Переименовать `_variant_key` → `variant_key` (единственное внутреннее использование — в `describe_event`, строка `key = _variant_key(stimulus)` → `key = variant_key(stimulus)`).
2. Удалить целиком: словарь `_TEMPLATES`, `_recent_picks`, функцию `_pick`, функцию `fallback_line`.
3. Удалить ставшие лишними импорты: `import random`, `from collections import defaultdict, deque`.
4. Обновить докстринг модуля: `"""LoL-колорит: описания событий, сленг-блок для промпта."""` (шаблоны переехали в templates/).

В `src/stream_director/games/lol/module.py` привести к виду:

```python
"""Сборка игрового модуля LoL: поллер + маппер + память + колорит."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...config import Settings
from ...stimulus import Stimulus
from ..base import GameModule
from ..template_pool import TemplatePool
from .client import LiveClientPoller
from .event_log import LolEventLog
from .flavor import describe_event, flavor_lines, joke_angles, variant_key
from .mapper import LolMapper
from .memory import LolSessionMemory

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_module(
    settings: Settings,
    submit: Callable[[Stimulus], None],
    on_live: Callable[[], None] | None = None,
) -> GameModule:
    mapper = LolMapper(submit=submit, event_log=LolEventLog())
    client = LiveClientPoller(
        getattr(settings, "lol_url", "https://127.0.0.1:2999"),
        on_payload=mapper.handle_payload,
        on_live=on_live,
    )
    # Пул на время жизни модуля = сессия приложения: сброс — перезапуск.
    pool = TemplatePool(_TEMPLATES_DIR, variant_key)
    return GameModule(
        id="lol",
        display_name="League of Legends",
        source=client,
        memory=LolSessionMemory(),
        describe_event=describe_event,
        flavor_lines=flavor_lines,
        fallback_line=lambda s: pool.take(s) or pool.exhausted_pick(s),
        always_speak_types=frozenset({"battle_start", "death", "multikill"}),
        diag=lambda: mapper.diag,
        joke_angles=joke_angles,
        template_pool=pool,
    )
```

- [ ] **Step 6: Package data в pyproject.toml**

После секции `[tool.setuptools.packages.find]` добавить:

```toml
[tool.setuptools.package-data]
"stream_director.games.lol" = ["templates/*.txt"]
```

- [ ] **Step 7: Прогнать тесты**

Run: `python -m pytest tests/test_template_pool.py tests/test_lol_flavor.py -v`
Expected: все PASS

Run: `python -m pytest -q` (весь набор — ничего чужого не сломали; `tests/test_games_base.py` может проверять поля `GameModule` — если упал, добавить в его ожидания новое опциональное поле)
Expected: PASS

---

### Task 3: seed_line в build_prompt

**Files:**
- Modify: `src/stream_director/commentary/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces (для Task 5): `build_prompt(module, stimulus, memory_lines, session_lines=None, recent_lines=None, seed_line: str | None = None) -> str`. При `seed_line` в промпте есть маркер `Заготовка шутки:` и НЕТ строки `Угол шутки`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_prompts.py`:

```python
def test_seed_line_block_present_and_replaces_angle():
    # Затравка сама задаёт угол — случайный «угол шутки» не подсказываем,
    # иначе LLM получает две конфликтующие инструкции.
    module = dataclasses.replace(MODULE, joke_angles=lambda: ("угол-тест",))
    p = build_prompt(module, game("frag"), [], seed_line="Минус один!")
    assert "Заготовка шутки: «Минус один!»" in p
    assert "Угол шутки" not in p


def test_no_seed_block_by_default():
    p = build_prompt(MODULE, game("frag"), [])
    assert "Заготовка шутки" not in p
```

- [ ] **Step 2: Убедиться, что падают**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL — `TypeError: build_prompt() got an unexpected keyword argument 'seed_line'`

- [ ] **Step 3: Реализовать**

В `src/stream_director/commentary/prompts.py`:

Сигнатура:

```python
def build_prompt(
    module: "GameModule",
    stimulus: Stimulus,
    memory_lines: list[str],
    session_lines: list[str] | None = None,
    recent_lines: list[str] | None = None,
    seed_line: str | None = None,
) -> str:
```

Заменить блок с углом шутки (строки 89–92) на:

```python
    parts.append(f"Обращение к стримеру на этот раз: {random.choice(_ADDRESS_STYLES)}.")
    if seed_line:
        # Затравка задаёт угол сама — случайный угол не подсказываем.
        parts.append(
            f"Заготовка шутки: «{seed_line}». Разверни её в свою реплику: "
            "сохрани соль, адаптируй под текущий контекст боя, можешь перефразировать."
        )
    else:
        angles = module.joke_angles() if module.joke_angles else ()
        if angles:
            parts.append(f"Угол шутки на этот раз: {random.choice(angles)}.")
```

- [ ] **Step 4: Прогнать тесты**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: все PASS

---

### Task 4: Settings.template_mode + API

**Files:**
- Modify: `src/stream_director/config.py`
- Modify: `src/stream_director/server.py`
- Test: `tests/test_config.py`, `tests/test_server.py`

**Interfaces:**
- Produces (для Task 5 и Task 6): `Settings.template_mode: str = "seed"`; `PUT /api/settings` принимает `template_mode`, отвергает неизвестные значения с 400; `GET /api/settings` возвращает поле.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_config.py`:

```python
def test_template_mode_default_and_validation(tmp_path):
    assert Settings().template_mode == "seed"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"template_mode": "чепуха"}), encoding="utf-8")
    # Неизвестное значение из старого/битого файла — тихий откат на дефолт.
    assert load_settings(path).template_mode == "seed"
    path.write_text(json.dumps({"template_mode": "verbatim"}), encoding="utf-8")
    assert load_settings(path).template_mode == "verbatim"
```

Добавить в `tests/test_server.py` (рядом с другими тестами настроек):

```python
@pytest.mark.asyncio
async def test_settings_template_mode_roundtrip_and_validation(client, ctx):
    r = await client.put("/api/settings", json={"template_mode": "verbatim"})
    assert r.status_code == 200
    assert r.json()["template_mode"] == "verbatim"
    assert ctx.settings.template_mode == "verbatim"
    r = await client.put("/api/settings", json={"template_mode": "чепуха"})
    assert r.status_code == 400
```

- [ ] **Step 2: Убедиться, что падают**

Run: `python -m pytest tests/test_config.py tests/test_server.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'template_mode'` и 200-с-игнором/ошибка валидации.

- [ ] **Step 3: Реализовать**

В `src/stream_director/config.py`:

В dataclass `Settings` после `voice_overrides`:

```python
    # Шаблоны-заготовки LoL: "seed" — затравка для LLM, "verbatim" — сначала
    # дословно, "off" — только фолбэк при мёртвой LLM.
    template_mode: str = "seed"
```

В `load_settings` заменить хвост функции:

```python
    try:
        settings = Settings(**data)
    except TypeError:
        log.warning("settings.json contains invalid values, using defaults")
        return Settings()
    if settings.template_mode not in ("seed", "verbatim", "off"):
        log.warning("неизвестный template_mode %r — беру 'seed'", settings.template_mode)
        settings.template_mode = "seed"
    return settings
```

В `src/stream_director/server.py`:

В `SettingsIn` после `voice_overrides`:

```python
    template_mode: str | None = None
```

В `put_settings` после валидации `llm_provider`:

```python
        if "template_mode" in data and data["template_mode"] not in ("seed", "verbatim", "off"):
            raise HTTPException(400, "template_mode must be 'seed', 'verbatim' or 'off'")
```

- [ ] **Step 4: Прогнать тесты**

Run: `python -m pytest tests/test_config.py tests/test_server.py -v`
Expected: все PASS

---

### Task 5: Ветвление режимов в директоре

**Files:**
- Modify: `src/stream_director/director.py:124-137`
- Test: `tests/test_director.py`

**Interfaces:**
- Consumes: `module.template_pool` (Task 2), `build_prompt(..., seed_line=...)` (Task 3), `settings.template_mode` (Task 4).
- Produces: поведение по спеке; наружных интерфейсов не добавляет.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_director.py` (импорт `build_lol` там уже есть):

```python
def lol_game(type_="frag", **payload):
    payload.setdefault("target", "Darius")
    return Stimulus(kind="game_event", type=type_, game="lol",
                    priority=Priority.HIGH, payload=payload)


class NoneBackend(FakeBackend):
    async def generate(self, prompt):
        self.prompts.append(prompt)
        return None


def exhaust(pool, stimulus):
    while pool.take(stimulus) is not None:
        pass


@pytest.mark.asyncio
async def test_verbatim_mode_speaks_template_without_llm():
    backend = FakeBackend()
    d, published = make_director(backend=backend, template_mode="verbatim")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.submit(lol_game())
    await drain(d)
    assert len(published) == 1
    assert backend.prompts == []  # LLM не трогали
    assert published[0][0] in lol.template_pool.templates["frag"]


@pytest.mark.asyncio
async def test_verbatim_mode_falls_to_llm_when_exhausted():
    backend = FakeBackend(reply="сгенерировано")
    d, published = make_director(backend=backend, template_mode="verbatim")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    exhaust(lol.template_pool, lol_game())
    d.submit(lol_game())
    await drain(d)
    assert published[0][0] == "сгенерировано"
    assert "Заготовка шутки" not in backend.prompts[-1]  # затравки нет — пул пуст


@pytest.mark.asyncio
async def test_seed_mode_puts_template_into_prompt():
    backend = FakeBackend()
    d, published = make_director(backend=backend, template_mode="seed")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.submit(lol_game())
    await drain(d)
    assert len(published) == 1
    prompt = backend.prompts[-1]
    assert "Заготовка шутки:" in prompt
    assert "Угол шутки" not in prompt  # затравка вытесняет случайный угол


@pytest.mark.asyncio
async def test_seed_mode_dead_llm_speaks_seed_verbatim():
    backend = NoneBackend()
    d, published = make_director(backend=backend, template_mode="seed")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.submit(lol_game())
    await drain(d)
    assert len(published) == 1
    # В эфир ушла сама затравка (она же была в промпте), второй шаблон не тратим.
    assert published[0][0] in backend.prompts[-1]
    assert published[0][0] in lol.template_pool.templates["frag"]


@pytest.mark.asyncio
async def test_off_mode_no_seed_but_fallback_consumes_pool():
    backend = NoneBackend()
    d, published = make_director(backend=backend, template_mode="off")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.submit(lol_game())
    await drain(d)
    assert "Заготовка шутки" not in backend.prompts[-1]
    first = published[0][0]
    assert first in lol.template_pool.templates["frag"]
    # Фолбэк расходует общий пул: та же реплика не прозвучит второй раз.
    d.submit(lol_game())
    await drain(d)
    assert published[1][0] != first


@pytest.mark.asyncio
async def test_templates_shared_pool_across_modes():
    # Один пул на сессию: сказанное в verbatim не станет затравкой в seed.
    backend = FakeBackend()
    d, published = make_director(backend=backend, template_mode="verbatim")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.submit(lol_game())
    await drain(d)
    spoken = published[0][0]
    d.settings.template_mode = "seed"  # горячая смена режима
    d.submit(lol_game())
    await drain(d)
    assert spoken not in backend.prompts[-1]


@pytest.mark.asyncio
async def test_wot_module_without_pool_unaffected():
    # У WoT пула нет — verbatim не меняет его поведение.
    backend = FakeBackend()
    d, published = make_director(backend=backend, template_mode="verbatim")
    d.submit(game("frag"))
    await drain(d)
    assert published[0][0] == "реплика"  # обычная генерация


@pytest.mark.asyncio
async def test_chat_order_ignores_templates():
    backend = FakeBackend()
    d, published = make_director(backend=backend, template_mode="verbatim")
    lol = build_lol(Settings(), submit=lambda s: None)
    d.register(lol)
    d.tracker.mark_live("lol")
    d.submit(Stimulus(kind="chat_order", type="dir",
                      payload={"text": "скажи привет", "username": "u"}))
    await drain(d)
    assert len(published) == 1
    assert published[0][0] == "реплика"  # ответ LLM, не шаблон
```

Замечание для test_templates_shared_pool_across_modes: `spoken` — конкретная строка шаблона; проверяем, что она не попала в затравку следующего промпта. Теоретически LLM-часть промпта её содержать не может — затравка там единственное место с шаблонами.

- [ ] **Step 2: Убедиться, что падают**

Run: `python -m pytest tests/test_director.py -v`
Expected: новые тесты FAIL (шаблон не в эфире / затравки нет в промпте); старые PASS.

- [ ] **Step 3: Реализовать ветвление**

В `src/stream_director/director.py` заменить блок строк 124–137 (от комментария «Реплика отталкивается…» до `return True` после `fallback_line`) на:

```python
        # Реплика отталкивается от текущего боя; сессия — редкая подколка.
        memory_lines = facts + module.memory.battle_lines()
        want_session = random.random() < self.SESSION_TEASE_PROB
        session_lines = module.memory.session_lines() if want_session else []

        # Шаблоны-заготовки: дословно в эфир (verbatim), затравкой в промпт
        # (seed) или только как фолбэк (off). Чат-заказы шаблоны не трогают.
        pool = module.template_pool if stimulus.kind == "game_event" else None
        mode = self.settings.template_mode
        seed: str | None = None
        text: str | None = None
        if pool is not None and mode == "verbatim":
            text = pool.take(stimulus)
        if text is None:
            if pool is not None and mode == "seed":
                seed = pool.take(stimulus)
            prompt = build_prompt(module, stimulus, memory_lines, session_lines,
                                  recent_lines=list(self._recent_replicas),
                                  seed_line=seed)
            text = await self.backend.generate(prompt)
            if text is None:
                if stimulus.kind == "chat_order" and stimulus.type == "dir":
                    return True  # свободный заказ шаблоном не подменяем
                # Затравка уже выбрана — её и говорим, второй шаблон не тратим.
                text = seed if seed is not None else module.fallback_line(stimulus)
                if text is None:
                    return True
```

- [ ] **Step 4: Прогнать тесты**

Run: `python -m pytest tests/test_director.py -v`
Expected: все PASS

Run: `python -m pytest -q`
Expected: весь набор PASS

---

### Task 6: Селектор режима в панели

**Files:**
- Modify: `web/src/shared/api.ts` (интерфейс `SettingsDto`)
- Modify: `web/src/panel/Panel.tsx` (секция «Режиссёр»)

**Interfaces:**
- Consumes: `PUT /api/settings` c `template_mode` (Task 4).
- Produces: элемент управления; JS-тестов в проекте нет — проверка типов и сборка.

- [ ] **Step 1: Тип в api.ts**

В `web/src/shared/api.ts`, в `SettingsDto` после `voice_overrides`:

```typescript
  template_mode: "seed" | "verbatim" | "off";
```

- [ ] **Step 2: Селектор в Panel.tsx**

В секции `<h2>Режиссёр</h2>`, после закрывающего `</div>` блока `row wrap` (после чекбокса «Команды всем…») добавить:

```tsx
        <label className="check">
          Шаблоны реплик (LoL)
          <select
            value={settings.template_mode}
            onChange={(e) =>
              patch({ template_mode: e.target.value as SettingsDto["template_mode"] })
            }
          >
            <option value="seed">затравка для LLM (уникальные реплики)</option>
            <option value="verbatim">сначала дословно, потом LLM</option>
            <option value="off">только при сбое LLM</option>
          </select>
        </label>
```

Импорт `SettingsDto` в Panel.tsx уже есть (строка 2).

- [ ] **Step 3: Проверить сборку фронта**

Run: `cd web && rtk npm run build`
Expected: `tsc -b` без ошибок типов, vite build успешен.

---

### Task 7: Финальная проверка

**Files:** нет изменений.

- [ ] **Step 1: Полный прогон Python-тестов**

Run: `python -m pytest -q` (из корня)
Expected: все PASS, ноль упавших.

- [ ] **Step 2: Дымовая проверка шаблонов в пакете**

Run: `python -c "import sys; sys.path.insert(0,'src'); from stream_director.games.lol.module import build_module; from stream_director.config import Settings; m = build_module(Settings(), submit=lambda s: None); print(len(m.template_pool.templates), 'ключей')"`
Expected: `18 ключей`

- [ ] **Step 3: (опционально) E2E**

Если доступен skill `verify` — прогнать E2E с фейковыми источниками и убедиться, что реплики выходят в overlay-WebSocket в режимах seed и verbatim.
