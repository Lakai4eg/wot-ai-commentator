# LoL Ally Teasing + Replica Variety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новые события про союзников (фид/керри/отставание команды), контекст команды в промпте, антиповтор LLM-реплик и расширенные фолбэк-шаблоны для LoL.

**Architecture:** Маппер LoL читает счёт тиммейтов из снапшота `allPlayers` и порождает новые стимулы; память получает таблицу союзников через тихий стимул `team_state`; общий промпт-билдер получает историю реплик и ротацию «углов шутки»; фолбэк-шаблоны расширяются с выбором без повтора. Спека: `docs/superpowers/specs/2026-07-11-lol-ally-teasing-design.md`.

**Tech Stack:** Python 3.12, pytest (+pytest-asyncio), без новых зависимостей.

## Global Constraints

- НЕ коммитить в git ни в одной задаче — единый коммит делает оркестратор в конце (правило пользователя перекрывает шаг «Commit»).
- Все комментарии в коде и docstrings — по-русски, в стиле существующих файлов.
- Реплики/шаблоны: одно короткое предложение, дружеская подколка без токсичности, без клише «ну что ж», «классика жанра», «как всегда».
- Тесты запускать из корня репозитория: `python -m pytest tests/<файл> -v`.
- Не трогать файлы вне списка задачи (в репозитории есть чужие незакоммиченные изменения: `chat/twitch.py`, `main.py`, `server.py`, `web/`, `tests/test_server.py`, `tests/test_twitch_chat.py` — их не изменять и не «чинить»).

## Граф зависимостей задач

- Task 1 — первой (общее ядро: поле `joke_angles`, параметр `recent_lines`).
- После Task 1 параллельно 4 потока (файлы не пересекаются):
  - A: Task 2 (director.py)
  - B: Task 3 → Task 4 → Task 5 (mapper.py, последовательно)
  - C: Task 6 (memory.py)
  - D: Task 7 → Task 8 (flavor.py + module.py, последовательно)
- Task 9 (интеграционная проверка) — после всех.

---

### Task 1: Ядро промпта — история реплик и углы шутки

**Files:**
- Modify: `src/stream_director/games/base.py` (dataclass `GameModule`)
- Modify: `src/stream_director/commentary/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces: `GameModule.joke_angles: Callable[[], tuple[str, ...]] | None = None` (новое опциональное поле, последнее в dataclass).
- Produces: `build_prompt(module, stimulus, memory_lines, session_lines=None, recent_lines=None)` — новый необязательный параметр `recent_lines: list[str] | None`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_prompts.py` (импорты `dataclasses` и `game`/`MODULE` уже есть в файле, добавить `import dataclasses` в шапку):

```python
def test_recent_lines_block_present():
    p = build_prompt(MODULE, game("frag"), [],
                     recent_lines=["Первая реплика.", "Вторая реплика."])
    assert "не повторяй" in p.lower()
    assert "Первая реплика." in p and "Вторая реплика." in p


def test_no_recent_block_when_empty():
    p = build_prompt(MODULE, game("frag"), [], recent_lines=[])
    assert "не повторяй" not in p.lower()


def test_joke_angle_line_when_module_provides():
    module = dataclasses.replace(MODULE, joke_angles=lambda: ("угол-тест",))
    p = build_prompt(module, game("frag"), [])
    assert "Угол шутки на этот раз: угол-тест." in p


def test_no_joke_angle_without_field():
    # WoT-модуль поле не задаёт — строки угла быть не должно.
    p = build_prompt(MODULE, game("frag"), [])
    assert "Угол шутки" not in p
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: 4 новых теста FAIL (`TypeError: unexpected keyword argument 'recent_lines'` / `joke_angles`), старые PASS.

- [ ] **Step 3: Реализация**

В `src/stream_director/games/base.py` добавить последним полем dataclass `GameModule` (после `diag`):

```python
    # Подсказки «угла шутки» для промпта; None — модуль ротацию не использует.
    joke_angles: Callable[[], tuple[str, ...]] | None = None
```

В `src/stream_director/commentary/prompts.py` заменить сигнатуру и тело `build_prompt`:

```python
def build_prompt(
    module: "GameModule",
    stimulus: Stimulus,
    memory_lines: list[str],
    session_lines: list[str] | None = None,
    recent_lines: list[str] | None = None,
) -> str:
```

После блока `session_lines` (перед веткой `chat_order`) добавить:

```python
    if recent_lines:
        parts.append("Твои последние реплики — НЕ повторяй их формулировки, образы и шутки:")
        parts.extend(f"- {line}" for line in recent_lines)
        parts.append("")
```

После строки с `_ADDRESS_STYLES` (перед `parts.append(_RULES)`) добавить:

```python
    angles = module.joke_angles() if module.joke_angles else ()
    if angles:
        parts.append(f"Угол шутки на этот раз: {random.choice(angles)}.")
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_prompts.py tests/test_games_base.py -v`
Expected: все PASS.

---

### Task 2: Директор — история озвученных реплик

**Files:**
- Modify: `src/stream_director/director.py`
- Test: `tests/test_director.py`

**Interfaces:**
- Consumes: `build_prompt(..., recent_lines=...)` из Task 1.
- Produces: `Director._recent_replicas: deque[str]` (maxlen=8), пополняется каждой озвученной репликой (LLM и фолбэк).

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_director.py`:

```python
@pytest.mark.asyncio
async def test_recent_replicas_fed_back_into_prompt():
    """Прошлые реплики попадают в следующий промпт с запретом повтора."""
    backend = FakeBackend(reply="Уникальная шутка про фраг")
    d, published = make_director(backend)
    d.submit(game("frag"))
    await drain(d)
    d.submit(game("frag"))
    await drain(d)
    assert len(published) == 2
    assert "Уникальная шутка про фраг" in backend.prompts[1]
    assert "не повторяй" in backend.prompts[1].lower()
    # В первый промпт истории ещё нет.
    assert "не повторяй" not in backend.prompts[0].lower()
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `python -m pytest tests/test_director.py::test_recent_replicas_fed_back_into_prompt -v`
Expected: FAIL (в промпте нет блока истории).

- [ ] **Step 3: Реализация**

В `src/stream_director/director.py`:

1. В импорты добавить `from collections import deque`.
2. В `__init__` после `self._replica_times`:

```python
        # Последние озвученные реплики — в промпт, чтобы LLM не повторялась.
        self._recent_replicas: deque[str] = deque(maxlen=8)
```

3. Заменить вызов `build_prompt`:

```python
        prompt = build_prompt(module, stimulus, memory_lines, session_lines,
                              recent_lines=list(self._recent_replicas))
```

4. После второй проверки `if stimulus.expired(): return True` (перед `self._last_replica_at = time.time()`) добавить:

```python
        self._recent_replicas.append(text)
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_director.py -v`
Expected: все PASS.

---

### Task 3: Маппер — счёт союзников, тихий `team_state`, `ally_feeding`, глобальный интервал

**Files:**
- Modify: `src/stream_director/games/lol/mapper.py`
- Test: `tests/test_lol_mapper.py`

**Interfaces:**
- Produces: стимул `team_state` (silent, payload `{"allies": [{"champion", "kills", "deaths"}], "silent": True}`) — потребляется памятью (Task 6).
- Produces: стимул `ally_feeding` (payload `{"champion", "deaths"}`, NORMAL, ttl 20).
- Produces: приватные помощники `LolMapper._ally_event_ok()`, `LolMapper._emit_ally(type_, payload, ttl_s)` — используют Task 4 и Task 5.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_lol_mapper.py` (используются существующие константы `ME/ALLY/ENEMY`):

```python
def team_payload(ally=(0, 0), me_scores=(0, 0, 0), enemy=(0, 0),
                 game_time=100.0, events=()):
    """Снапшот с настраиваемыми счетами. ally/enemy — (kills, deaths)."""
    k, d, a = me_scores
    return {
        "activePlayer": {
            "riotId": ME,
            "championStats": {"currentHealth": 1000.0, "maxHealth": 1000.0},
        },
        "allPlayers": [
            {"riotId": ME, "championName": "Garen", "team": "ORDER",
             "isDead": False, "scores": {"kills": k, "deaths": d, "assists": a}},
            {"riotId": ALLY, "championName": "Lux", "team": "ORDER",
             "isDead": False,
             "scores": {"kills": ally[0], "deaths": ally[1], "assists": 0}},
            {"riotId": ENEMY, "championName": "Darius", "team": "CHAOS",
             "isDead": False,
             "scores": {"kills": enemy[0], "deaths": enemy[1], "assists": 0}},
        ],
        "events": {"Events": list(events)},
        "gameData": {"gameMode": "CLASSIC", "mapName": "Map11",
                     "gameTime": game_time},
    }


def types_of(stims):
    return [s.type for s in stims]


def test_team_state_silent_on_score_change():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))  # первый снапшот — тихий синк
    assert "team_state" not in types_of(stims)
    m.handle_payload(team_payload(ally=(1, 0)))  # счёт изменился
    ts = [s for s in stims if s.type == "team_state"]
    assert len(ts) == 1
    assert ts[0].payload["silent"] is True
    assert ts[0].payload["allies"] == [{"champion": "Lux", "kills": 1, "deaths": 0}]
    m.handle_payload(team_payload(ally=(1, 0)))  # без изменений — молчим
    assert len([s for s in stims if s.type == "team_state"]) == 1


def test_ally_feeding_thresholds_5_8_11():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))
    for deaths in range(1, 12):
        m._last_ally_event_at = 0.0  # обнуляем интервал — проверяем сами пороги
        m.handle_payload(team_payload(ally=(0, deaths)))
    feeds = [s for s in stims if s.type == "ally_feeding"]
    assert [f.payload["deaths"] for f in feeds] == [5, 8, 11]
    assert feeds[0].payload["champion"] == "Lux"


def test_ally_events_global_interval():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))
    m.handle_payload(team_payload(ally=(0, 5)))   # фид — озвучен, интервал взведён
    m.handle_payload(team_payload(ally=(0, 8)))   # порог достигнут, но интервал держит
    feeds = [s for s in stims if s.type == "ally_feeding"]
    assert [f.payload["deaths"] for f in feeds] == [5]
    m._last_ally_event_at = 0.0                   # интервал «вышел»
    m.handle_payload(team_payload(ally=(0, 8)))   # порог не потерян — озвучивается
    feeds = [s for s in stims if s.type == "ally_feeding"]
    assert [f.payload["deaths"] for f in feeds] == [5, 8]


def test_ally_trackers_reset_on_new_game():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0), game_time=100.0))
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(0, 5), game_time=200.0))
    assert types_of(stims).count("ally_feeding") == 1
    # Время пошло назад — новый матч, пороги забыты.
    m.handle_payload(team_payload(ally=(0, 0), game_time=5.0))
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(0, 5), game_time=20.0))
    assert types_of(stims).count("ally_feeding") == 2


def test_no_ally_events_for_enemies():
    m, stims = make()
    m.handle_payload(team_payload(enemy=(0, 0)))
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(enemy=(9, 9)))  # фидит противник — не наша тема
    assert "ally_feeding" not in types_of(stims)
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_lol_mapper.py -v -k "team_state or ally"`
Expected: новые тесты FAIL (`AttributeError: _last_ally_event_at` / нет событий).

- [ ] **Step 3: Реализация**

В `src/stream_director/games/lol/mapper.py`:

1. Константы после `_DEATH_DEDUP_S`:

```python
# Подколки союзников: пороги и глобальный интервал (анти-спам TTS).
_ALLY_FEED_START = 5     # первая подколка фидера
_ALLY_FEED_STEP = 3      # дальше на каждых +3 смертях (5, 8, 11…)
_ALLY_LEAD_MIN_KILLS = 8  # керри: минимум киллов союзника…
_ALLY_LEAD_GAP = 5        # …и отрыв от стримера
_ALLY_EVENT_INTERVAL_S = 60.0  # не чаще одного союзного события в минуту
_TEAM_GAP_TIME_S = 600.0       # «наблюдатель»: стример 0/0/0 к 10-й минуте
_TEAM_GAP_TEAM_KILLS = 5       # …при этом команда уже навела шороху
_TEAM_GAP_BEHIND_DIFF = 10     # команда отстаёт по киллам
```

2. В конец `_reset_game_trackers`:

```python
        # Счёт союзников (kills, deaths) по ключу имени; None до первого
        # снапшота — синхронизируемся молча, историю не переигрываем.
        self._ally_scores: dict[str, tuple[int, int]] | None = None
        self._ally_feed_voiced: dict[str, int] = {}  # последний озвученный порог
        self._ally_lead_voiced: set[str] = set()
        self._team_gap_voiced: set[str] = set()
        self._last_ally_event_at = 0.0
```

3. В `handle_payload` после `self._process_snapshot(data, me)`:

```python
        self._process_team(data, me, players)
```

4. Новые методы (после `_process_snapshot`):

```python
    # --- команда: счёт союзников и поводы для подколок -----------------------

    def _ally_event_ok(self) -> bool:
        """Глобальный интервал союзных событий — не спамим озвучкой."""
        return time.time() - self._last_ally_event_at >= _ALLY_EVENT_INTERVAL_S

    def _emit_ally(self, type_: str, payload: dict, ttl_s: float = 20.0) -> None:
        self._last_ally_event_at = time.time()
        self._emit(type_, payload, Priority.NORMAL, ttl_s=ttl_s)

    def _collect_team(self, me: dict, players: list) -> tuple[list[dict], int]:
        """Союзники стримера (кроме него) и суммарные киллы противника."""
        my_team = me.get("team")
        me_key = self._name_key(me.get("riotId") or me.get("summonerName"))
        allies: list[dict] = []
        enemy_kills = 0
        for p in players:
            if not isinstance(p, dict):
                continue
            scores = p.get("scores") or {}
            kills = int(scores.get("kills") or 0)
            deaths = int(scores.get("deaths") or 0)
            if p.get("team") != my_team:
                enemy_kills += kills
                continue
            key = self._name_key(p.get("riotId") or p.get("summonerName"))
            if not key or key == me_key:
                continue
            allies.append({"key": key, "champion": p.get("championName") or "союзник",
                           "kills": kills, "deaths": deaths})
        return allies, enemy_kills

    def _process_team(self, data: dict, me: dict | None, players: list) -> None:
        if me is None or not me.get("team"):
            return
        allies, enemy_kills = self._collect_team(me, players)
        if not allies:
            return

        fresh = {a["key"]: (a["kills"], a["deaths"]) for a in allies}
        if self._ally_scores is None:
            self._ally_scores = fresh  # первый снапшот: молча синхронизируемся
        elif fresh != self._ally_scores:
            self._ally_scores = fresh
            # Тихий стимул: память получает таблицу союзников, реплики нет.
            self._emit("team_state",
                       {"allies": [{k: a[k] for k in ("champion", "kills", "deaths")}
                                   for a in allies],
                        "silent": True},
                       Priority.LOW, ttl_s=30)

        self._check_ally_feeding(allies)

    def _check_ally_feeding(self, allies: list[dict]) -> None:
        for a in allies:
            if not self._ally_event_ok():
                return  # интервал не вышел — пороги не сжигаем, дождёмся снапшота
            deaths = a["deaths"]
            if deaths < _ALLY_FEED_START:
                continue
            threshold = (_ALLY_FEED_START
                         + (deaths - _ALLY_FEED_START) // _ALLY_FEED_STEP * _ALLY_FEED_STEP)
            if threshold > self._ally_feed_voiced.get(a["key"], 0):
                self._ally_feed_voiced[a["key"]] = threshold
                self._emit_ally("ally_feeding",
                                {"champion": a["champion"], "deaths": deaths})
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_mapper.py -v`
Expected: все PASS (в т.ч. старые — первый снапшот синхронизируется молча и не ломает их).

---

### Task 4: Маппер — `ally_carrying` (мультикилл союзника + отрыв по киллам)

**Files:**
- Modify: `src/stream_director/games/lol/mapper.py`
- Test: `tests/test_lol_mapper.py`

**Interfaces:**
- Consumes: `_emit_ally`, `_ally_event_ok`, `_collect_team`, `_ally_lead_voiced` из Task 3; `team_payload`/`types_of` из тестов Task 3.
- Produces: стимул `ally_carrying` — два вида payload: `{"champion", "label", "count"}` (мультикилл союзника) и `{"champion", "kills", "my_kills"}` (отрыв).

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_lol_mapper.py`:

```python
def test_ally_multikill_becomes_ally_carrying():
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "Multikill", "KillerName": ALLY, "KillStreak": 3},
    ], game_time=5.0))
    carry = [s for s in stims if s.type == "ally_carrying"]
    assert len(carry) == 1
    assert carry[0].payload == {"champion": "Lux", "label": "трипл-килл", "count": 3}
    # Своё multikill-событие не подменилось.
    assert "multikill" not in types_of(stims)


def test_enemy_multikill_ignored():
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "Multikill", "KillerName": ENEMY, "KillStreak": 3},
    ], game_time=5.0))
    assert "ally_carrying" not in types_of(stims)


def test_ally_kill_lead_fires_once_per_game():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(8, 0), me_scores=(2, 0, 0)))
    carry = [s for s in stims if s.type == "ally_carrying"]
    assert len(carry) == 1
    assert carry[0].payload == {"champion": "Lux", "kills": 8, "my_kills": 2}
    # Отрыв растёт дальше — но подколка уже была, повторов нет.
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(12, 0), me_scores=(2, 0, 0)))
    assert len([s for s in stims if s.type == "ally_carrying"]) == 1


def test_ally_kill_lead_needs_both_thresholds():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))
    m._last_ally_event_at = 0.0
    # Отрыв 5, но киллов только 6 (< 8) — рано.
    m.handle_payload(team_payload(ally=(6, 0), me_scores=(1, 0, 0)))
    # Киллов 9, но отрыв 4 (< 5) — тоже рано.
    m.handle_payload(team_payload(ally=(9, 0), me_scores=(5, 0, 0)))
    assert "ally_carrying" not in types_of(stims)
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_lol_mapper.py -v -k carrying`
Expected: FAIL (событий `ally_carrying` нет).

- [ ] **Step 3: Реализация**

В `src/stream_director/games/lol/mapper.py`:

1. Заменить ветку `Multikill` в `_dispatch_event`:

```python
        elif name == "Multikill":
            killer = ev.get("KillerName")
            streak = int(ev.get("KillStreak") or 2)
            label = _MULTIKILL_LABELS.get(streak, "мультикилл")
            if self._is_me(killer, me):
                self._emit(
                    "multikill",
                    {"count": streak, "label": label},
                    Priority.CRITICAL if streak >= 5 else Priority.HIGH,
                    ttl_s=20,
                )
            elif (self._side_of(killer, me, players) == "ours"
                  and self._ally_event_ok()):
                # Мультикилл союзника: хвалим его, подкалываем стримера.
                self._emit_ally("ally_carrying",
                                {"champion": self._champion_of(killer, players),
                                 "label": label, "count": streak}, ttl_s=15)
```

2. В `_process_team` после `self._check_ally_feeding(allies)` добавить:

```python
        my_scores = me.get("scores") or {}
        my_kills = int(my_scores.get("kills") or 0)
        self._check_ally_lead(allies, my_kills)
```

3. Новый метод после `_check_ally_feeding`:

```python
    def _check_ally_lead(self, allies: list[dict], my_kills: int) -> None:
        for a in allies:
            if not self._ally_event_ok():
                return
            if (a["kills"] >= _ALLY_LEAD_MIN_KILLS
                    and a["kills"] - my_kills >= _ALLY_LEAD_GAP
                    and a["key"] not in self._ally_lead_voiced):
                # Один раз за игру на союзника: мишень шутки — стример.
                self._ally_lead_voiced.add(a["key"])
                self._emit_ally("ally_carrying",
                                {"champion": a["champion"], "kills": a["kills"],
                                 "my_kills": my_kills}, ttl_s=15)
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_mapper.py -v`
Expected: все PASS.

---

### Task 5: Маппер — `team_gap` (наблюдатель и отставание команды)

**Files:**
- Modify: `src/stream_director/games/lol/mapper.py`
- Test: `tests/test_lol_mapper.py`

**Interfaces:**
- Consumes: `_emit_ally`, `_ally_event_ok`, `_team_gap_voiced`, `_collect_team` из Task 3; `my_kills` уже вычисляется в `_process_team` (Task 4).
- Produces: стимул `team_gap` — payload `{"kind": "spectator", "team_kills"}` или `{"kind": "behind", "diff"}`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_lol_mapper.py`:

```python
def test_team_gap_spectator_once():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0), game_time=100.0))
    m._last_ally_event_at = 0.0
    # До 10-й минуты — рано, даже если команда воюет.
    m.handle_payload(team_payload(ally=(6, 0), game_time=500.0))
    assert "team_gap" not in types_of(stims)
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(6, 0), game_time=650.0))
    gaps = [s for s in stims if s.type == "team_gap"]
    assert len(gaps) == 1
    assert gaps[0].payload == {"kind": "spectator", "team_kills": 6}
    # Повторно не срабатывает.
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(7, 0), game_time=700.0))
    assert len([s for s in stims if s.type == "team_gap"
                and s.payload["kind"] == "spectator"]) == 1


def test_team_gap_spectator_needs_zero_score():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0), game_time=100.0))
    m._last_ally_event_at = 0.0
    # У стримера есть ассист — он не «наблюдатель».
    m.handle_payload(team_payload(ally=(6, 0), me_scores=(0, 0, 1), game_time=650.0))
    assert "team_gap" not in types_of(stims)


def test_team_gap_behind_once():
    m, stims = make()
    m.handle_payload(team_payload(ally=(0, 0)))
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(1, 0), me_scores=(1, 0, 0), enemy=(9, 0)))
    assert "team_gap" not in types_of(stims)  # разрыв 7 — мало
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(1, 0), me_scores=(1, 0, 0), enemy=(12, 0)))
    gaps = [s for s in stims if s.type == "team_gap"]
    assert len(gaps) == 1
    assert gaps[0].payload == {"kind": "behind", "diff": 10}
    m._last_ally_event_at = 0.0
    m.handle_payload(team_payload(ally=(1, 0), me_scores=(1, 0, 0), enemy=(15, 0)))
    assert len([s for s in stims if s.type == "team_gap"]) == 1
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_lol_mapper.py -v -k team_gap`
Expected: FAIL (событий `team_gap` нет).

- [ ] **Step 3: Реализация**

В `_process_team` заменить хвост (после `self._check_ally_feeding(allies)`) на:

```python
        my_scores = me.get("scores") or {}
        my_kills = int(my_scores.get("kills") or 0)
        my_deaths = int(my_scores.get("deaths") or 0)
        my_assists = int(my_scores.get("assists") or 0)
        self._check_ally_lead(allies, my_kills)

        team_kills = my_kills + sum(a["kills"] for a in allies)
        if ("spectator" not in self._team_gap_voiced and self._ally_event_ok()
                and self._game_time >= _TEAM_GAP_TIME_S
                and my_kills == my_deaths == my_assists == 0
                and team_kills >= _TEAM_GAP_TEAM_KILLS):
            self._team_gap_voiced.add("spectator")
            self._emit_ally("team_gap",
                            {"kind": "spectator", "team_kills": team_kills})

        if ("behind" not in self._team_gap_voiced and self._ally_event_ok()
                and enemy_kills - team_kills >= _TEAM_GAP_BEHIND_DIFF):
            self._team_gap_voiced.add("behind")
            self._emit_ally("team_gap",
                            {"kind": "behind", "diff": enemy_kills - team_kills})
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_mapper.py -v`
Expected: все PASS.

---

### Task 6: Память — таблица союзников и строки про заметных

**Files:**
- Modify: `src/stream_director/games/lol/memory.py`
- Test: `tests/test_lol_memory.py`

**Interfaces:**
- Consumes: стимул `team_state` (payload `{"allies": [{"champion", "kills", "deaths"}]}`) из Task 3 — но зависимость только на форму payload, кода маппера не требует.
- Produces: `LolBattleMemory.allies: list[dict]`; строки вида `союзник {champion}: {kills}/{deaths} — фидит|тащит` в `battle_lines()` (максимум 2).

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_lol_memory.py` (использовать существующий в файле helper создания стимулов, если есть; иначе добавить):

```python
def stim(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol",
                    priority=Priority.NORMAL, payload=payload)


def test_team_state_notable_allies_in_lines():
    mem = LolSessionMemory()
    mem.register(stim("battle_start", champion="Garen"))
    mem.register(stim("team_state", silent=True, allies=[
        {"champion": "Yasuo", "kills": 2, "deaths": 7},
        {"champion": "Lee Sin", "kills": 9, "deaths": 1},
        {"champion": "Sona", "kills": 1, "deaths": 1},
    ]))
    lines = mem.battle_lines()
    assert "союзник Lee Sin: 9/1 — тащит" in lines
    assert "союзник Yasuo: 2/7 — фидит" in lines
    assert not any("Sona" in line for line in lines)  # обычный счёт — не шумим


def test_ally_lines_capped_at_two():
    mem = LolSessionMemory()
    mem.register(stim("battle_start", champion="Garen"))
    mem.register(stim("team_state", silent=True, allies=[
        {"champion": f"Champ{i}", "kills": 0, "deaths": 6} for i in range(4)
    ]))
    ally_lines = [line for line in mem.battle_lines() if line.startswith("союзник")]
    assert len(ally_lines) == 2


def test_allies_reset_on_battle_start():
    mem = LolSessionMemory()
    mem.register(stim("team_state", silent=True,
                      allies=[{"champion": "Yasuo", "kills": 0, "deaths": 9}]))
    mem.register(stim("battle_start", champion="Garen"))
    assert not any("Yasuo" in line for line in mem.battle_lines())
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_lol_memory.py -v -k "team_state or ally_lines or allies_reset"`
Expected: FAIL (строк про союзников нет).

- [ ] **Step 3: Реализация**

В `src/stream_director/games/lol/memory.py`:

1. В `LolBattleMemory.__init__` добавить:

```python
        # Последняя таблица союзников из team_state: {champion, kills, deaths}.
        self.allies: list[dict] = []
```

2. В `LolBattleMemory.lines()` перед блоком `top = self.killers.most_common(1)` добавить:

```python
        notable: list[str] = []
        for a in self.allies:
            kills = int(a.get("kills") or 0)
            deaths = int(a.get("deaths") or 0)
            champ = a.get("champion") or "союзник"
            if kills >= 6:
                notable.append(f"союзник {champ}: {kills}/{deaths} — тащит")
            elif deaths >= 4:
                notable.append(f"союзник {champ}: {kills}/{deaths} — фидит")
        out.extend(notable[:2])  # не больше двух строк — контекст, не простыня
```

3. В `LolSessionMemory.register` добавить ветку (после `elif t == "low_hp":`):

```python
        elif t == "team_state":
            b.allies = list(p.get("allies") or [])
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_memory.py -v`
Expected: все PASS.

---

### Task 7: Колорит LoL — описания новых событий, ключи вариантов, выбор без повтора, углы шутки

**Files:**
- Modify: `src/stream_director/games/lol/flavor.py`
- Modify: `src/stream_director/games/lol/module.py`
- Test: `tests/test_lol_flavor.py`

**Interfaces:**
- Consumes: поле `GameModule.joke_angles` из Task 1.
- Produces: `joke_angles() -> tuple[str, ...]`; `_variant_key(stimulus) -> str` (ключи `ally_carrying_multikill|ally_carrying_lead|team_gap_spectator|team_gap_behind|objective_ours|objective_theirs|ace_ours|ace_theirs`); `_pick(key, options)` — выбор без повтора последних 3. Task 8 наполняет `_TEMPLATES` по этим ключам.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_lol_flavor.py`:

```python
def test_ally_event_descriptions():
    d = describe_event(game("ally_feeding", champion="Yasuo", deaths=8))
    assert "Yasuo" in d and "8" in d and "кормильц" in d.lower()
    d = describe_event(game("ally_carrying", champion="Lee Sin",
                            label="трипл-килл", count=3))
    assert "Lee Sin" in d and "трипл-килл" in d
    d = describe_event(game("ally_carrying", champion="Lee Sin",
                            kills=9, my_kills=2))
    assert "9" in d and "стример" in d.lower()
    d = describe_event(game("team_gap", kind="spectator", team_kills=6))
    assert "0/0/0" in d
    d = describe_event(game("team_gap", kind="behind", diff=12))
    assert "12" in d


def test_fallback_covers_new_ally_types():
    cases = (
        game("ally_feeding", champion="Yasuo", deaths=8),
        game("ally_carrying", champion="Lee Sin", label="трипл-килл", count=3),
        game("ally_carrying", champion="Lee Sin", kills=9, my_kills=2),
        game("team_gap", kind="spectator", team_kills=6),
        game("team_gap", kind="behind", diff=12),
    )
    for stim in cases:
        line = fallback_line(stim)
        assert isinstance(line, str) and line and line != "Без комментариев."


def test_fallback_objective_sides_differ():
    ours = {fallback_line(game("objective", side="ours")) for _ in range(30)}
    theirs = {fallback_line(game("objective", side="theirs")) for _ in range(30)}
    assert ours and theirs and ours.isdisjoint(theirs)


def test_joke_angles_wired_into_module():
    m = build_module(Settings(), submit=lambda s: None)
    angles = m.joke_angles()
    assert len(angles) >= 8 and all(isinstance(a, str) for a in angles)


def test_flavor_mentions_ally_targets():
    text = flavor_lines()
    assert "фидер" in text.lower() or "союзник" in text.lower()
```

- [ ] **Step 2: Запустить тесты, убедиться в падении**

Run: `python -m pytest tests/test_lol_flavor.py -v`
Expected: новые FAIL (описания начинаются с «Событие:», `joke_angles` нет), старые PASS.

- [ ] **Step 3: Реализация flavor.py**

В `src/stream_director/games/lol/flavor.py`:

1. Импорты: `from collections import defaultdict, deque`.

2. В `_FLAVOR` добавить пункт (после пункта про сленг):

```python
    "\n- Союзники — законная мишень: фидер («кормилец» команды), керри, который "
    "тащит, джунглер без ганков. Дружески, без токсичности: смеёмся вместе."
```

(добавить строку в скобочную конкатенацию `_FLAVOR`).

3. В `_EVENT_DESCRIPTIONS` добавить записи:

```python
    "ally_feeding": (
        "Союзник стримера на чемпионе {champion} набрал уже {deaths} смертей. "
        "Подколи союзника-«кормильца» команды — стримера в этом не вини."
    ),
    "ally_carrying_multikill": (
        "Союзник {champion} собрал {label}! Похвали союзника, а подколи стримера: "
        "пока он смотрел, играли за него."
    ),
    "ally_carrying_lead": (
        "Союзник {champion} набрал {kills} убийств против {my_kills} у стримера. "
        "Мишень шутки — стример (кто-то тащит вместо него), союзника хвали."
    ),
    "team_gap_spectator": (
        "Игра идёт уже больше десяти минут, у команды {team_kills} убийств, "
        "а у стримера всё ещё 0/0/0. Подколи стримера-наблюдателя."
    ),
    "team_gap_behind": (
        "Команда стримера отстаёт от противника на {diff} убийств. "
        "Подколи всю команду разом — по-доброму, без злобы."
    ),
```

4. Новая функция (перед `describe_event`):

```python
def _variant_key(stimulus: Stimulus) -> str:
    """Ключ шаблона/описания: часть событий ветвится по payload."""
    t, p = stimulus.type, stimulus.payload
    if t == "ally_carrying":
        return "ally_carrying_multikill" if p.get("label") else "ally_carrying_lead"
    if t == "team_gap":
        return f"team_gap_{p.get('kind', 'behind')}"
    if t == "objective" and p.get("side") in ("ours", "theirs"):
        return f"objective_{p['side']}"
    if t == "ace":
        return "ace_theirs" if p.get("side") == "theirs" else "ace_ours"
    return t
```

5. В `describe_event`:
   - добавить к setdefault-блоку: `p.setdefault("champion", "союзник")`, `p.setdefault("deaths", "?")`, `p.setdefault("kills", "?")`, `p.setdefault("my_kills", "?")`, `p.setdefault("team_kills", "?")`, `p.setdefault("diff", "?")`.
     ВАЖНО: `p["champion_ru"] = p.get("champion") or "своего чемпиона"` стоит ДО setdefault — иначе battle_start получит «союзник». Порядок: сначала существующая строка `champion_ru`, потом `p.setdefault("champion", "союзник")`.
   - заменить строку выбора шаблона на:

```python
    key = _variant_key(stimulus)
    template = (_EVENT_DESCRIPTIONS.get(key)
                or _EVENT_DESCRIPTIONS.get(stimulus.type, f"Событие: {stimulus.type}."))
```

6. Выбор без повтора и новый `fallback_line` (заменить существующий):

```python
# Последние выданные варианты на ключ — не повторяем свежие фолбэки.
_recent_picks: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=3))


def _pick(key: str, options: list[str]) -> str:
    recent = _recent_picks[key]
    pool = [o for o in options if o not in recent]
    if not pool:  # вариантов мало — хотя бы не повторяем последний
        pool = [o for o in options if not recent or o != recent[-1]] or list(options)
    choice = random.choice(pool)
    recent.append(choice)
    return choice


def fallback_line(stimulus: Stimulus) -> str:
    key = _variant_key(stimulus)
    options = _TEMPLATES.get(key) or _TEMPLATES.get(stimulus.type)
    if not options:
        return "Без комментариев."
    return _pick(key, options)
```

7. Углы шутки (после `flavor_lines`):

```python
_JOKE_ANGLES = (
    "подколи союзника, если контекст даёт повод (фидер, керри, джунглер)",
    "самоирония: ты ИИ без рук, а туда же — комментируешь",
    "обыграй текущий счёт стримера",
    "бытовая метафора: сравни происходящее с чем-то из обычной жизни",
    "пафос киберспортивного кастера — с лёгким перегибом",
    "«чат уже печатает» — представь реакцию зрителей",
    "обыграй чемпиона противника или его судьбу",
    "похвала с подвохом: комплимент, который смешит",
    "сухая констатация факта — юмор в невозмутимости",
    "обыграй ферму, варды или макро-детали",
)


def joke_angles() -> tuple[str, ...]:
    return _JOKE_ANGLES
```

8. ВРЕМЕННО до Task 8: чтобы `fallback_line` для новых типов не возвращал «Без комментариев.», добавить в `_TEMPLATES` минимальные списки (Task 8 заменит их полными):

```python
    "ally_feeding": ["Наш союзник опять кормит — щедрая душа."],
    "ally_carrying_multikill": ["Мультикилл у союзника! Приятно посмотреть — со стороны."],
    "ally_carrying_lead": ["В команде нашёлся керри. Спойлер: это не стример."],
    "team_gap_spectator": ["Команда воюет, наш герой медитирует — у каждого свой путь."],
    "team_gap_behind": ["Счёт уехал в пользу врага — самое время для легендарного камбэка."],
    "objective_ours": ["Объект наш! Кто-то в команде играет от макро."],
    "objective_theirs": ["Объект у противника. Кто-то забыл поставить будильник."],
    "ace_ours": ["Эйс! Целая команда на серых экранах."],
    "ace_theirs": ["Эйс у противника — вся наша команда дружно на серых экранах."],
```

Списки `objective` и `ace` в `_TEMPLATES` пока не удалять (Task 8 переработает).

9. В `src/stream_director/games/lol/module.py`: импортировать `joke_angles` из `.flavor` и передать в `GameModule(... , joke_angles=joke_angles)` (последним аргументом).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_flavor.py tests/test_prompts.py -v`
Expected: все PASS (`test_fallback_objective_sides_differ` проходит уже сейчас — по одному варианту на сторону).

---

### Task 8: Колорит LoL — полный набор фолбэк-шаблонов (8–12 вариантов)

**Files:**
- Modify: `src/stream_director/games/lol/flavor.py`
- Test: `tests/test_lol_flavor.py`

**Interfaces:**
- Consumes: ключи `_variant_key` из Task 7.
- Produces: финальный `_TEMPLATES` — единственный источник фолбэков LoL.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_lol_flavor.py`:

```python
def test_templates_are_rich_and_unique():
    from stream_director.games.lol.flavor import _TEMPLATES
    rich = ("battle_start", "frag", "death", "assist", "multikill", "first_blood",
            "turret", "inhib", "ace_ours", "objective_ours", "objective_theirs")
    for key in rich:
        assert len(_TEMPLATES[key]) >= 8, key
    new = ("ally_feeding", "ally_carrying_multikill", "ally_carrying_lead",
           "team_gap_spectator", "team_gap_behind", "ace_theirs")
    for key in new:
        assert len(_TEMPLATES[key]) >= 5, key
    for key, options in _TEMPLATES.items():
        assert len(set(options)) == len(options), f"дубликаты в {key}"


def test_fallback_no_repeat_last_three():
    picks = [fallback_line(game("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `python -m pytest tests/test_lol_flavor.py::test_templates_are_rich_and_unique -v`
Expected: FAIL (списки короткие).

- [ ] **Step 3: Заменить `_TEMPLATES` целиком**

Заменить весь словарь `_TEMPLATES` в `src/stream_director/games/lol/flavor.py` на:

```python
_TEMPLATES: dict[str, list[str]] = {
    "battle_start": [
        "На Ущелье прибыл сильнейший — Годжо снял повязку, Шесть Глаз уже читают миникарту.",
        "«Прошу прощения» — сильнейший вышел на линию, вражеские миньоны напряглись.",
        "Бесконечность активна: до нашего героя пару минут не дотянется даже минион.",
        "Годжо Сатору явился на Summoner's Rift — вот теперь официально начинаем.",
        "Сильнейший на карте уже здесь — остальным девятерым остаётся красиво проиграть.",
        "Повязка снята, наносекунды пошли — Рифт к такому не готовился.",
        "Явление сильнейшего: вражеская команда ещё не знает, что уже извиняется.",
        "Шесть Глаз видят всё — кроме, пожалуй, вардов, но начало положено.",
    ],
    "frag": [
        "Минус один. Ферма подождёт.",
        "Килл! В клиенте засчитано, в чате не верят.",
        "Противник отправлен на серую заставку.",
        "Убийство по учебнику — если бы такой учебник существовал.",
        "Один готов. Кто-то сейчас пишет «gank mid» заглавными.",
        "Противник ушёл на перерождение — подумать о своих решениях.",
        "Есть килл! Голда капнула, самооценка тоже.",
        "Минус чемпион — на той стороне кто-то тянется к кнопке «ff».",
        "Убийство засчитано: скромно киваем, будто так и планировали.",
        "Фраг! Красиво вышло, почти как задумывалось.",
    ],
    "death": [
        "Серый экран. Время подумать о жизни.",
        "Смерть по расписанию — таймер респауна уже тикает.",
        "Врагу тоже надо фармить голду — щедрость украшает.",
        "Таймер смерти — лучшее время пересмотреть жизненные приоритеты.",
        "Умер красиво. Жаль, засчитывается одинаково.",
        "Фонтан ждёт. Он всегда ждёт.",
        "Серый экран — единственное место на карте, где никто не ганкает.",
        "Смерть — это просто телепорт на базу с задержкой.",
        "Одна жизнь отдана науке: теперь известно, что так делать не надо.",
        "Респаун близко — противник пусть пока порадуется.",
    ],
    "assist": [
        "Ассист! Главное — вовремя постоять рядом.",
        "Помог, засчитано. Командная игра, надо же.",
        "Ассист: полкилла по факту, целый килл по ощущениям.",
        "Постоял рядом с успехом — успех засчитан.",
        "Ассист в копилку: кто-то же должен подсвечивать чужие подвиги.",
        "Помощь пришла вовремя — редкий случай, отмечаем.",
        "Ассист! В послематчевой таблице будет выглядеть солидно.",
        "Соучастие доказано — плюс один ассист.",
    ],
    "multikill": [
        "Мультикилл! Кто вы и куда дели нашего стримера?",
        "Серия убийств! Клипы сами себя не нарежут — скриньте.",
        "Мультикилл — противники решили уходить организованно, колонной.",
        "Двое, трое… счётчик еле успевает.",
        "Серия! Где-то тихо плачет вражеский саппорт.",
        "Мультикилл — вот это уже похоже на хайлайт.",
        "Убийства оптом: сегодня выгодный курс.",
        "Серия фрагов — миникарта краснеет, чат зеленеет от зависти.",
    ],
    "first_blood": [
        "Первая кровь! Кто-то уже пишет «gg» в чат.",
        "First blood — самый громкий звук в этой игре.",
        "Первая кровь пролита — теперь это официально не фарм-симулятор.",
        "First blood! Матч проснулся.",
        "Первая кровь — лучшая заявка на настроение всей игры.",
        "Кровь пролита, ставки сделаны — понеслась.",
        "Первая кровь: чей-то ранний план уже отправился в корзину.",
        "First blood — и весь Рифт сделал вид, что так и было задумано.",
    ],
    "objective": [
        "На карте забрали объект — подробности выясняются.",
        "Объект пал. Чей — история умалчивает, таблица расскажет.",
        "Кто-то взял объект: миникарта знает больше, чем говорит.",
    ],
    "objective_ours": [
        "Объект наш! Кто-то в команде играет от макро.",
        "Забрали объект — смайт сегодня на стороне добра.",
        "Плюс объект команде: медленно, но уверенно.",
        "Объект в копилке — таблица после игры разберётся, чей вклад.",
        "Взяли объект, даже слаженно — подозрительно.",
        "Объект забран: макроигра замечена на нашей половине карты.",
        "Команда собралась и забрала объект — редкое природное явление.",
        "Объект наш, тайминг соблюдён — кто-то явно смотрел гайды.",
    ],
    "objective_theirs": [
        "Объект у противника. Кто-то забыл поставить будильник.",
        "Противник забрал объект — наша команда наблюдала с уважением.",
        "Минус объект: враг играет в макро, наша команда — в ожидание.",
        "Объект уехал к противнику. Им, видимо, нужнее.",
        "Враг забрал объект: таймер стоял, команда тоже.",
        "Объект отдан без боя — щедрость, достойная отдельной похвалы.",
        "Противник собрал объект по расписанию — хоть у кого-то есть план.",
        "Объект у врага, зато у нас… сейчас придумаем, что у нас.",
    ],
    "turret": [
        "Башня снесена. Голда капнула — настроение поднялось.",
        "Минус башня. Пуш идёт по плану, что подозрительно.",
        "Башня пала — архитектура противника несёт потери.",
        "Плюс башня в резюме: линия становится уютнее.",
        "Башня демонтирована. Строители расстроятся.",
        "Снос засчитан — у противника стало меньше недвижимости.",
        "Башня рухнула, голда разлетелась по карманам команды.",
        "Ещё одна башня в отчёт — пуш работает.",
    ],
    "inhib": [
        "Ингибитор упал! Суперминьоны уже собирают вещи.",
        "Минус ингибитор — база противника открыта нараспашку.",
        "Ингибитор снесён: суперминьоны выходят на смену.",
        "Ингибитор пал — у противника дома сквозняк.",
        "Есть ингибитор! База врага переходит на осадное положение.",
        "Ингибитор в минусе — теперь миньоны сделают половину работы.",
        "Снесли ингибитор: противнику пора учить слово «дефенс».",
        "Ингибитор готов — осталось совсем немного до нексуса.",
    ],
    "ace_ours": [
        "Эйс! Целая команда на серых экранах.",
        "Пять могилок разом — тимфайт удался.",
        "Эйс: вся вражеская пятёрка дружно ушла на базу пешком.",
        "Эйс! Карта на минуту стала нашей дачей.",
        "Полный состав противника отдыхает — время ломать базу.",
        "Эйс — пять таймеров тикают, нексус нервничает.",
        "Вся пятёрка выключена: свет на базе противника мигает.",
        "Эйс! Такое надо клипать — потом никто не поверит.",
    ],
    "ace_theirs": [
        "Эйс у противника — вся наша команда дружно на серых экранах.",
        "Нас разобрали всей пятёркой. Синхронно — хоть на видео учись.",
        "Полный вайп команды: противник забирает всё, что не прибито.",
        "Эйс врага — пять таймеров, одна печаль.",
        "Команду смело целиком: можно спокойно сходить за чаем.",
    ],
    "ally_feeding": [
        "Наш союзник опять кормит — щедрая душа.",
        "Кто-то в команде открыл столовую для противника.",
        "Союзник фидит с душой — противник уже оставил чаевые.",
        "Счётчик смертей союзника растёт быстрее его фермы.",
        "У союзника переговоры с фонтаном явно затянулись.",
        "Кормилец команды снова в деле — враг сыт и доволен.",
        "Союзник продолжает спонсировать вражескую экономику.",
        "Ещё одна доставка голды противнику — союзник работает без выходных.",
    ],
    "ally_carrying_multikill": [
        "Мультикилл у союзника! Приятно посмотреть — со стороны.",
        "Союзник собрал серию — кто-то же в команде должен.",
        "Мультикилл тиммейта: наш герой вдохновенно наблюдал.",
        "Союзник разносит — стримеру остаётся кивать со знанием дела.",
        "Серия у союзника! Главное — вовремя встать рядом для ассиста.",
        "Мультикилл союзника — тащат, как заказывали.",
    ],
    "ally_carrying_lead": [
        "Союзник уносит игру — наш герой пока в роли зрителя с лучшим местом.",
        "Кто-то в команде решил сыграть за двоих — за кого второго, понятно.",
        "Союзник керрит так, что стримеру осталось только не мешать.",
        "Счёт союзника уехал вперёд — догонять уже неловко.",
        "В команде нашёлся керри. Спойлер: это не стример.",
        "Союзник тащит — наш герой обеспечивает моральную поддержку.",
    ],
    "team_gap_spectator": [
        "Десять минут, 0/0/0 — стример сегодня в роли военного корреспондента.",
        "Команда воюет, наш герой медитирует — у каждого свой путь.",
        "0/0/0 к десятой минуте: идеальная нейтральность, Швейцария одобряет.",
        "Пока команда делает киллы, стример коллекционирует впечатления.",
        "Нулевая строчка в таблице — зато какая ферма… наверное.",
        "Стример пока наблюдает: лучшие решения принимаются со стороны.",
    ],
    "team_gap_behind": [
        "Команда отстаёт на десяток киллов — зато у нас атмосфера лучше.",
        "Счёт уехал в пользу врага — самое время для легендарного камбэка.",
        "Минус десять по киллам: играем от обороны, так и запишем.",
        "Противник ушёл в отрыв — наша команда копит на реванш.",
        "Разрыв в счёте солидный — зато нервы у зрителей уже стальные.",
        "Отставание по киллам: команда явно затягивает интригу.",
    ],
}
```

Старые ключи `objective` (нейтральный) остаётся, `ace` удаляется — его заменяют `ace_ours`/`ace_theirs` (unknown-стороны у ace не бывает: маппер всегда ставит `side`).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_lol_flavor.py -v`
Expected: все PASS, включая `test_fallback_no_repeat_last_three` и `test_fallback_covers_all_lol_types` (bare `ace` маппится на `ace_ours` через `_variant_key`).

---

### Task 9: Интеграционная проверка

**Files:** нет изменений (только запуск).

- [ ] **Step 1: Полный прогон тестов**

Run: `python -m pytest`
Expected: все тесты PASS. Если падают тесты в незатронутых файлах (`test_server.py`, `test_twitch_chat.py` — чужие незакоммиченные изменения), зафиксировать это в отчёте, но НЕ чинить.

---

### Task 10: Версия и релиз (выполняет оркестратор, НЕ workflow-агент)

- [ ] Bump версии `0.1.3` → `0.2.0` в `pyproject.toml` (строка `version = "0.1.3"`) и `src/stream_director/__init__.py` (`__version__`).
- [ ] Закоммитить ТОЛЬКО файлы этой фичи (mapper/memory/flavor/module/base/prompts/director + тесты + docs + версия). Чужие изменения (`chat/twitch.py`, `main.py`, `server.py`, `web/`, `test_server.py`, `test_twitch_chat.py`) не трогать.
- [ ] `git push`, тег `v0.2.0`, `git push origin v0.2.0` — CI (`.github/workflows/release.yml`) прогонит тесты, соберёт portable-zip и опубликует релиз.
