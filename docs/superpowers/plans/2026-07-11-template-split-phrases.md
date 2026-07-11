# План: разделение шаблонных событий LoL и наполнение фраз до 20

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разделить шаблонные события LoL (объекты по видам и сторонам, башни по сторонам) и дополнить каждый из 26 файлов фраз до 20 реплик.

**Architecture:** Маппер (`mapper.py`) добавляет в payload машинные ключи (`kind_key` для объектов, `side` для башен по имени снесённой башни), `variant_key` в `flavor.py` разводит события по новым файлам шаблонов, `TemplatePool` подхватывает файлы без правок. Генерация фраз — параллельным Workflow (агент на файл) + сводная проверка.

**Tech Stack:** Python 3.11+, pytest; шаблоны — plain-text UTF-8 (строка = фраза, `#` — комментарий).

**Спека:** `docs/superpowers/specs/2026-07-11-template-split-phrases-design.md`

## Global Constraints

- **НИКОГДА не коммитить** (глобальное правило пользователя) — в задачах нет шагов commit.
- Тон фраз: сарказм и подколки, лёгкая токсичность допустима; мемы «пресс F», «клипаем», «чат уже печатает», смайт-дифф, «0/10 пауэрспайк», «гап», «инт», «мид дифф»; твич-мемы русифицированные («кекв», не «KEKW»); без плейсхолдеров `{…}`; фразы читает TTS вслух.
- В каждом файле шаблонов ровно 20 непустых строк-фраз (тест проверяет `>= 20` и уникальность).
- Существующие фразы сохраняются дословно.
- Команды запускать с префиксом `rtk` (правило пользователя): `rtk test python -m pytest …`.
- Кодировка файлов шаблонов — UTF-8 (пул читает `utf-8-sig`).

---

### Task 1: Mapper — kind_key у объектов и стороны башен

**Files:**
- Modify: `src/stream_director/games/lol/mapper.py` (строки ~39, ~286-302)
- Test: `tests/test_lol_mapper.py`

**Interfaces:**
- Produces: событие `objective` с payload `{"kind": str, "kind_key": "dragon"|"herald"|"baron", "side": "ours"|"theirs"|"unknown", "stolen": bool}`; событие `turret` с payload `{}` (лично стример) или `{"side": "ours"|"theirs"}` (команда взяла / нашу потеряли). Статический метод `LolMapper._turret_side(turret_name, me) -> str`.

- [ ] **Step 1: Дополнить тест объектов и написать новые тесты башен (падающие)**

В `tests/test_lol_mapper.py` в тесте `test_objectives_sides_and_steal` после строки `assert dragon.payload["side"] == "ours" and "дракон" in dragon.payload["kind"]` добавить:

```python
    assert dragon.payload["kind_key"] == "dragon"
    assert baron.payload["kind_key"] == "baron"
```

И добавить в конец файла новые тесты:

```python
def test_turret_sides_by_turret_name():
    # Сторона башни — по имени снесённой башни (T1=ORDER, T2=CHAOS), а не по
    # убийце: башни часто добивают миньоны, которых _side_of не сопоставит.
    m, stims = make()
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        # Союзник снёс вражескую башню (T2 = CHAOS, стример в ORDER) — «забрали».
        {"EventID": 1, "EventName": "TurretKilled", "KillerName": ALLY,
         "TurretKilled": "Turret_T2_L_03_A"},
        # Миньоны снесли нашу башню (T1 = ORDER) — «отдали», убийца не сопоставим.
        {"EventID": 2, "EventName": "TurretKilled",
         "KillerName": "Minion_T200_L1_S25", "TurretKilled": "Turret_T1_C_05_A"},
        # Стример добил лично — прежнее личное событие без стороны.
        {"EventID": 3, "EventName": "TurretKilled", "KillerName": ME,
         "TurretKilled": "Turret_T2_C_01_A"},
    ]
    m.handle_payload(payload(events=events, game_time=10.0))
    turrets = [s for s in stims if s.type == "turret"]
    assert [t.payload.get("side") for t in turrets] == ["ours", "theirs", None]


def test_turret_with_unknown_team_not_emitted():
    # Команду башни из имени не распознали, добил не стример — молчим,
    # сторону не выдумываем (прецедент ложных комментариев про дракона).
    m, stims = make()
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "TurretKilled",
         "KillerName": "SomeoneElse#XX1", "TurretKilled": "Obelisk_Weird"},
    ]
    m.handle_payload(payload(events=events, game_time=10.0))
    assert [s for s in stims if s.type == "turret"] == []
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `rtk test python -m pytest tests/test_lol_mapper.py -v`
Expected: FAIL — `test_objectives_sides_and_steal` (KeyError `kind_key`), `test_turret_sides_by_turret_name` (список сторон не совпадает: эмитится только личное событие).

- [ ] **Step 3: Реализация в mapper.py**

Заменить константу `_OBJECTIVE_KINDS` (строка ~39):

```python
# вид объекта: (русская строка для LLM, машинный ключ для шаблонов)
_OBJECTIVE_KINDS = {"DragonKill": ("дракон", "dragon"),
                    "HeraldKill": ("герольд", "herald"),
                    "BaronKill": ("барон", "baron")}
```

В `_dispatch_event` заменить ветки `elif name in _OBJECTIVE_KINDS:` и `elif name == "TurretKilled":` на:

```python
        elif name in _OBJECTIVE_KINDS:
            kind, kind_key = _OBJECTIVE_KINDS[name]
            if name == "DragonKill" and ev.get("DragonType"):
                kind = f"дракон ({ev['DragonType']})"
            stolen = str(ev.get("Stolen", "False")) == "True"
            self._emit(
                "objective",
                {"kind": kind, "kind_key": kind_key,
                 "side": self._side_of(ev.get("KillerName"), me, players),
                 "stolen": stolen},
                Priority.HIGH if stolen else Priority.NORMAL, ttl_s=15,
            )
        elif name == "TurretKilled":
            if self._is_me(ev.get("KillerName"), me):
                self._emit("turret", {}, Priority.NORMAL, ttl_s=15)
            else:
                side = self._turret_side(ev.get("TurretKilled"), me)
                if side in ("ours", "theirs"):
                    self._emit("turret", {"side": side}, Priority.NORMAL, ttl_s=15)
```

И добавить статический метод рядом с `_side_of`:

```python
    @staticmethod
    def _turret_side(turret_name: Any, me: dict | None) -> str:
        """ours — пала вражеская башня, theirs — наша.

        Сторону определяем по ИМЕНИ башни (Turret_T1_… принадлежит ORDER,
        Turret_T2_… — CHAOS), а не по убийце: последний удар часто за
        миньоном, которого _side_of не сопоставит с игроками.
        """
        my_team = (me or {}).get("team")
        name = str(turret_name or "")
        owner = "ORDER" if "_T1_" in name else "CHAOS" if "_T2_" in name else None
        if not my_team or owner is None:
            return "unknown"
        return "theirs" if owner == my_team else "ours"
```

- [ ] **Step 4: Тесты зелёные**

Run: `rtk test python -m pytest tests/test_lol_mapper.py -v`
Expected: PASS (все тесты файла).

---

### Task 2: Flavor — variant_key и описания башен

**Files:**
- Modify: `src/stream_director/games/lol/flavor.py` (`variant_key` ~65, `_EVENT_DESCRIPTIONS` ~22)
- Test: `tests/test_lol_flavor.py`

**Interfaces:**
- Consumes: payload из Task 1 (`kind_key`, `side` у turret).
- Produces: `variant_key` возвращает `objective_stolen_{side}`, `objective_{kind_key}_{side}`, `turret_{side}`; описания `turret_ours`/`turret_theirs` в `_EVENT_DESCRIPTIONS`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_lol_flavor.py`:

```python
def test_variant_key_objective_kinds_steals_and_turrets():
    from stream_director.games.lol.flavor import variant_key
    assert variant_key(game("objective", kind_key="dragon", side="ours")) == "objective_dragon_ours"
    assert variant_key(game("objective", kind_key="baron", side="theirs")) == "objective_baron_theirs"
    assert variant_key(game("objective", kind_key="herald", side="theirs")) == "objective_herald_theirs"
    # Крад важнее вида объекта.
    assert variant_key(game("objective", kind_key="baron", side="ours", stolen=True)) == "objective_stolen_ours"
    assert variant_key(game("objective", kind_key="dragon", side="theirs", stolen=True)) == "objective_stolen_theirs"
    # Сторона неизвестна — честный фолбэк, кто бы ни забрал.
    assert variant_key(game("objective", kind_key="dragon", side="unknown")) == "objective"
    # Старый payload без kind_key: пул откатится на файл objective сам.
    assert variant_key(game("objective", side="ours")) == "objective_ours"
    assert variant_key(game("turret", side="ours")) == "turret_ours"
    assert variant_key(game("turret", side="theirs")) == "turret_theirs"
    assert variant_key(game("turret")) == "turret"


def test_turret_side_descriptions():
    ours = describe_event(game("turret", side="ours"))
    theirs = describe_event(game("turret", side="theirs"))
    personal = describe_event(game("turret"))
    # Команда взяла башню — заслуга не лично стримера.
    assert "команда" in ours.lower() and "не" in ours.lower()
    # Нашу башню снесли.
    assert "противник" in theirs.lower()
    # Личное событие — прежний текст про стримера.
    assert "стример" in personal.lower()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `rtk test python -m pytest tests/test_lol_flavor.py -v -k "variant_key_objective or turret_side"`
Expected: FAIL — `variant_key` возвращает `objective_ours`/`turret`, описаний `turret_*` нет.

- [ ] **Step 3: Реализация в flavor.py**

В `variant_key` заменить ветку `if t == "objective" and p.get("side") in ("ours", "theirs"):` на:

```python
    if t == "objective" and p.get("side") in ("ours", "theirs"):
        side = p["side"]
        if p.get("stolen"):
            return f"objective_stolen_{side}"  # крад важнее вида объекта
        kind_key = p.get("kind_key")
        if kind_key in ("dragon", "baron", "herald"):
            return f"objective_{kind_key}_{side}"
        return f"objective_{side}"  # старый payload: пул откатится на objective
    if t == "turret" and p.get("side") in ("ours", "theirs"):
        return f"turret_{p['side']}"
```

В `_EVENT_DESCRIPTIONS` после ключа `"turret"` добавить:

```python
    "turret_ours": (
        "Команда стримера снесла башню противника — добил союзник или миньоны, "
        "лично стример её не добивал."
    ),
    "turret_theirs": "Противник снёс башню команды стримера.",
```

- [ ] **Step 4: Тесты зелёные**

Run: `rtk test python -m pytest tests/test_lol_flavor.py -v`
Expected: PASS (весь файл; старые тесты не задеты — файлы шаблонов ещё не тронуты).

---

### Task 3: Реструктуризация файлов шаблонов (сиды)

**Files:**
- Create: `src/stream_director/games/lol/templates/objective_dragon_ours.txt`, `objective_dragon_theirs.txt`, `objective_baron_ours.txt`, `objective_baron_theirs.txt`, `objective_herald_ours.txt`, `objective_herald_theirs.txt`, `objective_stolen_ours.txt`, `objective_stolen_theirs.txt`, `turret_ours.txt`, `turret_theirs.txt`
- Delete: `src/stream_director/games/lol/templates/objective_ours.txt`, `objective_theirs.txt`
- Modify: `tests/test_lol_flavor.py` (`test_templates_are_rich_and_unique`, `test_fallback_objective_sides_differ`)

**Interfaces:**
- Consumes: `variant_key` из Task 2 (имена файлов = ключи вариантов).
- Produces: 26 файлов шаблонов; 8 фраз из `objective_ours.txt` и 8 из `objective_theirs.txt` дословно распределены по новым файлам.

- [ ] **Step 1: Обновить тесты шаблонов (падающие)**

В `tests/test_lol_flavor.py` заменить `test_fallback_objective_sides_differ` на:

```python
def test_fallback_objective_sides_differ():
    m = lol_module()
    ours = {m.fallback_line(game("objective", kind_key="dragon", side="ours"))
            for _ in range(30)}
    theirs = {m.fallback_line(game("objective", kind_key="dragon", side="theirs"))
              for _ in range(30)}
    assert ours and theirs and ours.isdisjoint(theirs)
```

И заменить `test_templates_are_rich_and_unique` на:

```python
def test_templates_are_rich_and_unique():
    templates = lol_module().template_pool.templates
    # Спека 2026-07-11: события объектов и башен разделены по видам и сторонам.
    split_keys = (
        "objective_dragon_ours", "objective_dragon_theirs",
        "objective_baron_ours", "objective_baron_theirs",
        "objective_herald_ours", "objective_herald_theirs",
        "objective_stolen_ours", "objective_stolen_theirs",
        "turret_ours", "turret_theirs",
    )
    for key in split_keys:
        assert key in templates, key
    # Старые обобщённые файлы удалены — их фразы переехали в новые.
    assert "objective_ours" not in templates
    assert "objective_theirs" not in templates
    for key, options in templates.items():
        assert len(set(options)) == len(options), f"дубликаты в {key}"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `rtk test python -m pytest tests/test_lol_flavor.py -v -k "templates_are_rich or objective_sides_differ"`
Expected: FAIL — новых файлов нет, старые ещё на месте.

- [ ] **Step 3: Создать новые файлы с сидами, удалить старые**

Фразы из `objective_ours.txt` (дословно) распределяются:
- `objective_dragon_ours.txt`: строки 1, 2, 8 («Объект наш! Кто-то в команде играет от макро.», «Забрали объект — смайт сегодня на стороне добра.», «Объект наш, тайминг соблюдён — кто-то явно смотрел гайды.»)
- `objective_baron_ours.txt`: строки 4, 5, 7 («Объект в копилке — таблица после игры разберётся, чей вклад.», «Взяли объект, даже слаженно — подозрительно.», «Команда собралась и забрала объект — редкое природное явление.»)
- `objective_herald_ours.txt`: строки 3, 6 («Плюс объект команде: медленно, но уверенно.», «Объект забран: макроигра замечена на нашей половине карты.»)

Фразы из `objective_theirs.txt` (дословно):
- `objective_dragon_theirs.txt`: строки 1, 2, 5 («Объект у противника. Кто-то забыл поставить будильник.», «Противник забрал объект — наша команда наблюдала с уважением.», «Враг забрал объект: таймер стоял, команда тоже.»)
- `objective_baron_theirs.txt`: строки 3, 6, 8 («Минус объект: враг играет в макро, наша команда — в ожидание.», «Объект отдан без боя — щедрость, достойная отдельной похвалы.», «Объект у врага, зато у нас… сейчас придумаем, что у нас.»)
- `objective_herald_theirs.txt`: строки 4, 7 («Объект уехал к противнику. Им, видимо, нужнее.», «Противник собрал объект по расписанию — хоть у кого-то есть план.»)

`objective_stolen_ours.txt`, `objective_stolen_theirs.txt`, `turret_ours.txt`, `turret_theirs.txt` — создать по 2 стартовые фразы, чтобы `TemplatePool` их загрузил (пустые файлы пул выбрасывает):

`objective_stolen_ours.txt`:
```
Украли объект из-под смайта — вор в законе на нашей стороне!
Смайт-дифф в нашу пользу: объект уехал у противника из-под носа.
```

`objective_stolen_theirs.txt`:
```
Объект украден у нас из-под носа — смайт-дифф во всей красе.
Противник забрал наш объект последним ударом — клипаем и плачем.
```

`turret_ours.txt`:
```
Команда снесла башню — у противника минус недвижимость.
Башня противника пала усилиями команды — стример наблюдал, но тоже молодец.
```

`turret_theirs.txt`:
```
Нашу башню снесли. Недвижимость дешевеет на глазах.
Минус наша башня — противник занялся редевелопментом.
```

Удалить: `objective_ours.txt`, `objective_theirs.txt` (после переноса фраз).

- [ ] **Step 4: Тесты зелёные**

Run: `rtk test python -m pytest tests/test_lol_flavor.py tests/test_lol_mapper.py -v`
Expected: PASS.

---

### Task 4: Генерация фраз Workflow до 20 в каждом файле

**Files:**
- Modify: все 26 файлов `src/stream_director/games/lol/templates/*.txt`
- Test: `tests/test_lol_flavor.py` (`test_templates_are_rich_and_unique`)

**Interfaces:**
- Consumes: структуру файлов из Task 3.
- Produces: 26 файлов по 20 уникальных фраз.

- [ ] **Step 1: Ужесточить тест до 20 фраз (падающий)**

В `test_templates_are_rich_and_unique` заменить финальный цикл на:

```python
    for key, options in templates.items():
        assert len(options) >= 20, f"{key}: {len(options)} < 20"
        assert len(set(options)) == len(options), f"дубликаты в {key}"
```

Run: `rtk test python -m pytest tests/test_lol_flavor.py::test_templates_are_rich_and_unique -v`
Expected: FAIL (в файлах меньше 20 фраз).

- [ ] **Step 2: Запустить Workflow генерации**

Вызвать инструмент Workflow со скриптом: `pipeline` по 26 файлам, стадия 1 — агент на файл (читает файл, сохраняет существующие фразы дословно, дописывает новые до ровно 20 строк, перезаписывает файл), стадия 2 — не нужна барьером; после pipeline — один сводный агент-ревьюер: читает все 26 файлов, ищет кросс-файловые дубли/почти-дубли и заменяет их свежими фразами, проверяет счёт 20.

Контекст каждому агенту-генератору (в промпт):
- назначение файла (какое событие озвучивается — таблица из спеки);
- тон: сарказм, подколки, лёгкая токсичность допустима; мемы «пресс F», «клипаем», «чат уже печатает», смайт-дифф, «0/10 пауэрспайк», «гап», «инт», «мид дифф»; русифицированные твич-мемы (кекв — не KEKW); лексика LoL умеренно (ферма, ганк, вард, пуш, фид, скейлинг);
- формат: строка = одна фраза, без нумерации, без плейсхолдеров `{…}`, без кавычек-обёрток; фразу читает TTS вслух — «пресс F» ок, «KEKW» латиницей нельзя;
- контекст события: кто мишень шутки (потеряли башню — подкалываем команду; взяли — хвалим с сарказмом; стример лично — подкалываем/хвалим стримера);
- для `objective_stolen_*` фразы НЕ называют конкретный объект (общие для дракона/барона/герольда);
- требование мотива Годжо для `battle_start` удалено по решению пользователя (тест `test_battle_start_carries_gojo_bit` удалён, LLM-описание нейтральное); авторские фразы с Годжо в файле остаются;
- существующие строки не менять и не удалять.

- [ ] **Step 3: Валидация скриптом**

Написать и выполнить проверку в scratchpad (не в репозитории):

```python
import re, sys
from pathlib import Path

d = Path("src/stream_director/games/lol/templates")
ok = True
all_lines: dict[str, str] = {}
for f in sorted(d.glob("*.txt")):
    lines = [l.strip() for l in f.read_text(encoding="utf-8-sig").splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    if len(lines) != 20:
        ok = False; print(f"{f.name}: {len(lines)} фраз вместо 20")
    if len(set(lines)) != len(lines):
        ok = False; print(f"{f.name}: внутренние дубликаты")
    for l in lines:
        if re.search(r"[{}]", l):
            ok = False; print(f"{f.name}: плейсхолдер в '{l}'")
        if re.search(r"[A-Za-z]{4,}", l):  # длинные латинские слова = эмоуты/англицизмы
            print(f"{f.name}: латиница (проверить вручную): '{l}'")
        if l in all_lines:
            ok = False; print(f"дубль между {all_lines[l]} и {f.name}: '{l}'")
        all_lines[l] = f.name
sys.exit(0 if ok else 1)
```

Expected: exit 0; предупреждения о латинице разобрать вручную (имена вроде «пресс F» допустимы).

- [ ] **Step 4: Проверить сохранность исходных фраз**

Run: `rtk git diff -- src/stream_director/games/lol/templates/`
Expected: в diff нет удалённых строк-фраз, кроме переноса из `objective_ours/theirs.txt` (эти строки должны появиться добавленными в новых файлах).

- [ ] **Step 5: Полный прогон тестов**

Run: `rtk test python -m pytest`
Expected: PASS (вся сюита).

---

## Self-Review

- Спека покрыта: kind_key и башни (Task 1), variant_key и описания (Task 2), структура файлов и перенос фраз (Task 3), 20 фраз и Workflow (Task 4). Критерии готовности спеки → шаги 3-5 Task 4.
- Типы согласованы: payload `kind_key`/`side` из Task 1 совпадает с ветками `variant_key` Task 2; имена файлов Task 3 совпадают с ключами `variant_key`.
- Без коммитов — глобальное правило пользователя.
