"""Таблица «сообщение WotStat → ожидаемый стимул» для WotMapper.

FakeClient повторяет duck-typed интерфейс WotStatClient (get/subscribe/
on_trigger) из спеки §3, но без сети: тест сам «толкает» состояния (`set_state`)
и триггеры (`fire`), а маппер отдаёт стимулы в собранный список `emitted`.
"""

from __future__ import annotations

import pytest

from stream_director.stimulus import Priority
from stream_director.games.wot.mapper import WotMapper

MY_ID = 42
MY_TEAM = 1


class FakeClient:
    """Мини-клиент: хранит плоское дерево, раздаёт state- и trigger-коллбеки."""

    def __init__(self, initial: dict | None = None) -> None:
        self.state: dict = dict(initial or {})
        self._subs: dict[str, list] = {}
        self._trigs: dict[str, list] = {}

    def get(self, path, default=None):
        return self.state.get(path, default)

    def subscribe(self, path, cb):
        self._subs.setdefault(path, []).append(cb)

    def on_trigger(self, path, cb):
        self._trigs.setdefault(path, []).append(cb)

    # --- инструменты теста ---
    def set_state(self, path, value):
        old = self.state.get(path)
        self.state[path] = value
        for cb in self._subs.get(path, []):
            cb(value, old)

    def fire(self, path, value=None):
        for cb in self._trigs.get(path, []):
            cb(value)


def veh(name, player_id=None, team=2):
    """VehicleWithOwner-подобный объект."""
    return {
        "localizedShortName": name,
        "localizedName": name,
        "playerName": name,
        "playerId": player_id,
        "team": team,
        "tag": name,
    }


ME = veh("Streamer", MY_ID, MY_TEAM)


@pytest.fixture
def wired():
    """Клиент + маппер в состоянии «идёт бой»; собираем стимулы в список."""
    client = FakeClient(
        {
            "player.id": MY_ID,
            "player.name": "Streamer",
            "battle.arena.team": MY_TEAM,
            "battle.arena.localizedName": "Прохоровка",
            "battle.arena.mode": "standard",
            "battle.maxHealth": 1000,
            "battle.isAlive": True,
            "game.state": "battle",
        }
    )
    emitted: list = []
    mapper = WotMapper(client, emitted.append)
    return client, mapper, emitted


def only(emitted):
    assert len(emitted) == 1, f"ожидался один стимул, получено {len(emitted)}: {[s.type for s in emitted]}"
    return emitted[0]


# --- 4.1 события с репликами ---------------------------------------------


def test_damage_dealt(wired):
    client, _, emitted = wired
    client.fire(
        "battle.onDamage",
        {"attacker": ME, "target": veh("Tiger II"), "damage": 300, "health": 700, "reason": "shot"},
    )
    s = only(emitted)
    assert s.type == "damage_dealt"
    assert s.priority == Priority.NORMAL
    assert s.payload == {"amount": 300, "target": "Tiger II", "reason": "shot"}


def test_damage_dealt_low_when_small(wired):
    client, _, emitted = wired
    client.fire(
        "battle.onDamage",
        {"attacker": ME, "target": veh("Tiger II"), "damage": 100, "health": 900, "reason": "shot"},
    )
    assert only(emitted).priority == Priority.LOW


def test_damage_received(wired):
    client, _, emitted = wired
    client.fire(
        "battle.onDamage",
        {"attacker": veh("Rhm"), "target": ME, "damage": 200, "health": 800, "reason": "shot"},
    )
    s = only(emitted)
    assert s.type == "damage_received"
    assert s.priority == Priority.NORMAL
    assert s.payload == {"amount": 200, "source": "Rhm", "reason": "shot"}


def test_damage_received_low_when_small(wired):
    client, _, emitted = wired
    client.fire(
        "battle.onDamage",
        {"attacker": veh("Rhm"), "target": ME, "damage": 90, "health": 910, "reason": "shot"},
    )
    assert only(emitted).priority == Priority.LOW


def test_damage_received_from_arta_flagged(wired):
    """Прилёт от САУ помечается from_arta и не тонет в LOW-приоритете."""
    client, _, emitted = wired
    arta = veh("G.W. Tiger")
    arta["class"] = "SPG"
    client.fire(
        "battle.onDamage",
        {"attacker": arta, "target": ME, "damage": 90, "health": 910, "reason": "shot"},
    )
    s = only(emitted)
    assert s.type == "damage_received"
    assert s.payload["from_arta"] is True
    assert s.priority == Priority.NORMAL  # даже мелкий прилёт от арты — не LOW


def test_at_spg_is_not_arta(wired):
    """ПТ-САУ (AT-SPG) — не арта, обычный полученный урон."""
    client, _, emitted = wired
    pt = veh("Rhm.-B. WT")
    pt["class"] = "AT-SPG"
    client.fire(
        "battle.onDamage",
        {"attacker": pt, "target": ME, "damage": 400, "health": 600, "reason": "shot"},
    )
    assert "from_arta" not in only(emitted).payload


def test_foreign_damage_ignored(wired):
    client, _, emitted = wired
    # Ни атакующий, ни цель — не стример.
    client.fire(
        "battle.onDamage",
        {"attacker": veh("A"), "target": veh("B"), "damage": 500, "health": 100, "reason": "shot"},
    )
    assert emitted == []


def test_frag(wired):
    client, _, emitted = wired
    client.fire("battle.onPlayerFeedback", {"type": "kill", "data": {"vehicle": veh("IS-7")}})
    s = only(emitted)
    assert s.type == "frag"
    assert s.priority == Priority.HIGH
    assert s.payload == {"target": "IS-7"}


def test_death_uses_last_attacker(wired):
    client, _, emitted = wired
    # Сначала по стримеру бьёт «Убийца» — маппер запоминает обидчика.
    client.fire(
        "battle.onDamage",
        {"attacker": veh("Убийца"), "target": ME, "damage": 250, "health": 0, "reason": "shot"},
    )
    emitted.clear()
    client.set_state("battle.isAlive", False)
    s = only(emitted)
    assert s.type == "death"
    assert s.priority == Priority.HIGH
    assert s.payload == {"killer": "Убийца"}


def test_death_without_known_attacker(wired):
    client, _, emitted = wired
    client.set_state("battle.isAlive", False)
    s = only(emitted)
    assert s.type == "death"
    assert s.payload == {"killer": "неизвестный"}


def test_crit(wired):
    client, _, emitted = wired
    client.fire("battle.onPlayerFeedback", {"type": "crit", "data": {"critsCount": 2}})
    s = only(emitted)
    assert s.type == "crit"
    assert s.priority == Priority.LOW


def light_tank(name="ELC EVEN 90"):
    tank = veh(name)
    tank["class"] = "lightTank"
    return tank


def test_spotted_on_light_tank(wired):
    """Засвет комментируем только на ЛТ — светить его работа."""
    client, _, emitted = wired
    client.state["hangar.vehicle.info"] = light_tank()
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {"isVisible": True}})
    s = only(emitted)
    assert s.type == "spotted"
    assert s.priority == Priority.LOW


def test_spotted_dedup(wired):
    """Засвет частит — второй в пределах окна дедупа не даёт новой реплики."""
    client, _, emitted = wired
    client.state["hangar.vehicle.info"] = light_tank()
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {"isVisible": True}})
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {"isVisible": True}})
    s = only(emitted)
    assert s.type == "spotted"


def test_spotted_ignored_on_non_light_tank(wired):
    """На тяже засвет — не наша заслуга, реплики нет."""
    client, _, emitted = wired
    tank = veh("E 100")
    tank["class"] = "heavyTank"
    client.state["hangar.vehicle.info"] = tank
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {"isVisible": True}})
    assert emitted == []


def test_spotted_ignored_without_vehicle_info(wired):
    """Класс танка неизвестен — молчим (только ЛТ даёт реплику про засвет)."""
    client, _, emitted = wired
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {"isVisible": True}})
    assert emitted == []


def test_assist_growth_threshold(wired):
    client, _, emitted = wired
    client.set_state("battle.efficiency.assist", 100)  # <200 — молчим
    assert emitted == []
    client.set_state("battle.efficiency.assist", 250)  # дельта 250 ≥ 200 — реплика
    s = only(emitted)
    assert s.type == "assist"
    assert s.priority == Priority.LOW
    assert s.payload == {"amount": 250}
    emitted.clear()
    client.set_state("battle.efficiency.assist", 350)  # дельта от 250 → 100, молчим
    assert emitted == []
    client.set_state("battle.efficiency.assist", 500)  # дельта от 250 → 250, реплика
    assert only(emitted).payload == {"amount": 250}


def test_blocked_growth_threshold(wired):
    client, _, emitted = wired
    client.set_state("battle.efficiency.blocked", 150)
    assert emitted == []
    client.set_state("battle.efficiency.blocked", 400)
    s = only(emitted)
    assert s.type == "blocked"
    assert s.payload == {"amount": 400}


def test_fire_dedup(wired):
    client, _, emitted = wired
    client.fire(
        "battle.onDamage",
        {"attacker": veh("враг"), "target": ME, "damage": 30, "health": 970, "reason": "fire"},
    )
    # Пожар тикает — второй тик в пределах 15 с не даёт новой реплики.
    client.fire(
        "battle.onDamage",
        {"attacker": veh("враг"), "target": ME, "damage": 28, "health": 942, "reason": "fire"},
    )
    s = only(emitted)
    assert s.type == "fire"
    assert s.priority == Priority.HIGH
    # Пожар — не damage_received: отдельного события про урон нет.
    assert all(x.type == "fire" for x in emitted)


def test_damage_milestone(wired):
    client, _, emitted = wired
    client.set_state("battle.efficiency.damage", 500)  # ещё не 1000
    assert emitted == []
    client.set_state("battle.efficiency.damage", 1200)  # веха 1000
    s = only(emitted)
    assert s.type == "damage_milestone"
    assert s.priority == Priority.NORMAL
    assert s.payload == {"total": 1200}
    emitted.clear()
    client.set_state("battle.efficiency.damage", 1900)  # та же веха — молчим
    assert emitted == []
    client.set_state("battle.efficiency.damage", 2100)  # веха 2000
    assert only(emitted).payload == {"total": 2100}


def test_base_capture_ours(wired):
    client, _, emitted = wired
    # Растут очки на ЧУЖОЙ базе (команда 2) → захватываем мы (ours).
    client.set_state(
        "battle.teamBases",
        {"2": [{"baseID": 10, "points": 40, "timeLeft": 90, "invadersCount": 1, "capturingStopped": False}]},
    )
    s = only(emitted)
    assert s.type == "base_capture"
    assert s.priority == Priority.HIGH
    assert s.payload == {"side": "ours"}


def test_base_capture_theirs_and_dedup(wired):
    client, _, emitted = wired
    # Растут очки на НАШЕЙ базе (команда 1) → захватывает враг (theirs).
    client.set_state(
        "battle.teamBases",
        {"1": [{"baseID": 5, "points": 30, "timeLeft": 95, "invadersCount": 2, "capturingStopped": False}]},
    )
    s = only(emitted)
    assert s.payload == {"side": "theirs"}
    emitted.clear()
    # Дальнейший рост очков — дедуп, новой реплики нет.
    client.set_state(
        "battle.teamBases",
        {"1": [{"baseID": 5, "points": 60, "timeLeft": 80, "invadersCount": 2, "capturingStopped": False}]},
    )
    assert emitted == []
    # Сброс очков в 0 снимает дедуп.
    client.set_state(
        "battle.teamBases",
        {"1": [{"baseID": 5, "points": 0, "timeLeft": 100, "invadersCount": 0, "capturingStopped": True}]},
    )
    # Новый заход захвата — снова реплика.
    client.set_state(
        "battle.teamBases",
        {"1": [{"baseID": 5, "points": 25, "timeLeft": 96, "invadersCount": 1, "capturingStopped": False}]},
    )
    assert only(emitted).payload == {"side": "theirs"}


# --- 4.2 тихие события (silent) ------------------------------------------


def test_low_hp_silent_once(wired):
    client, _, emitted = wired
    client.set_state("battle.health", 150)  # <20% от 1000
    s = only(emitted)
    assert s.type == "low_hp"
    assert s.payload.get("silent") is True
    emitted.clear()
    client.set_state("battle.health", 100)  # всё ещё низко — но раз за бой
    assert emitted == []


def test_battle_start_silent_and_resets(wired):
    client, mapper, emitted = wired
    # Накопим состояние, которое должно сброситься на старте боя.
    mapper._last_attacker = "кто-то"
    mapper._assist_emitted = 999
    client.set_state("game.state", "loading")  # сначала уводим из battle
    emitted.clear()
    client.set_state("game.state", "battle")
    s = only(emitted)
    assert s.type == "battle_start"
    assert s.payload.get("silent") is True
    assert s.payload.get("map") == "Прохоровка"
    assert s.payload.get("mode") == "standard"
    # Пер-боевые трекеры сброшены.
    assert mapper._last_attacker is None
    assert mapper._assist_emitted == 0


def test_battle_result_win_silent(wired):
    client, _, emitted = wired
    client.fire("battle.onBattleResult", {"arenaUniqueID": 1, "common": {"winnerTeam": MY_TEAM}})
    s = only(emitted)
    assert s.type == "battle_result"
    assert s.payload.get("silent") is True
    assert s.payload.get("outcome") == "win"


def test_battle_result_loss(wired):
    client, _, emitted = wired
    client.fire("battle.onBattleResult", {"common": {"winnerTeam": 2}})
    assert only(emitted).payload.get("outcome") == "loss"


def test_battle_result_draw(wired):
    client, _, emitted = wired
    client.fire("battle.onBattleResult", {"common": {"winnerTeam": 0}})
    assert only(emitted).payload.get("outcome") == "draw"


def test_vehicle_change_silent(wired):
    client, _, emitted = wired
    client.set_state("hangar.vehicle.info", veh("T-34"))
    s = only(emitted)
    assert s.type == "vehicle_change"
    assert s.payload.get("silent") is True
    assert s.payload.get("tank") == "T-34"


def test_tier11_tank_triggers_joke(wired):
    """Танк 11 уровня даёт отдельную (не тихую) реплику-подколку."""
    client, _, emitted = wired
    tank = veh("Object 11")
    tank["level"] = 11
    client.set_state("hangar.vehicle.info", tank)
    types = [s.type for s in emitted]
    assert "vehicle_change" in types
    tier11 = [s for s in emitted if s.type == "tier11"]
    assert len(tier11) == 1
    assert tier11[0].payload == {"tank": "Object 11"}
    assert "silent" not in tier11[0].payload


def test_tier10_tank_no_joke(wired):
    """Обычный 10 уровень — только тихая смена танка, без подколки про 11."""
    client, _, emitted = wired
    tank = veh("E 100")
    tank["level"] = 10
    client.set_state("hangar.vehicle.info", tank)
    assert only(emitted).type == "vehicle_change"


# --- диагностика ----------------------------------------------------------


def test_diag_tracks_events(wired):
    client, mapper, emitted = wired
    client.state["hangar.vehicle.info"] = light_tank()
    client.fire("battle.onPlayerFeedback", {"type": "spotted", "data": {}})
    d = mapper.diag
    assert d["game_state"] == "battle"
    assert d["events_found"] == 1
    assert list(d["last_events"]) == ["spotted"]


def test_stimuli_stamped_with_wot(wired):
    client, _, emitted = wired
    client.fire("battle.onPlayerFeedback", {"type": "kill", "data": {"vehicle": veh("IS-7")}})
    assert emitted and all(s.game == "wot" for s in emitted)
