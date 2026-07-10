import time

from wot_ai_commentator.events import Priority, Stimulus
from wot_ai_commentator.games.wot.memory import SessionMemory


def ev(type_, **payload):
    return Stimulus(kind="game_event", type=type_, priority=Priority.NORMAL, payload=payload)


def test_repeated_killer_counted_with_fact():
    m = SessionMemory()
    m.register(ev("death", killer="арта"))
    m.register(ev("death", killer="арта"))
    facts = m.register(ev("death", killer="арта"))
    assert m.deaths == 3
    assert m.deaths_by_killer["арта"] == 3
    assert any("3" in f and "арта" in f for f in facts)


def test_damage_record_only_increases():
    m = SessionMemory()
    m.register(ev("damage_record", damage=3000))
    m.register(ev("damage_record", damage=2000))
    assert m.damage_record == 3000


def test_battle_results():
    m = SessionMemory()
    m.register(ev("battle_result", outcome="win"))
    m.register(ev("battle_result", outcome="loss"))
    assert m.battles == 2
    assert m.wins == 1


def test_summary_lines_after_events():
    m = SessionMemory()
    assert m.summary_lines() == []
    m.register(ev("frag"))
    m.register(ev("death", killer="ваншот"))
    lines = m.summary_lines()
    assert lines and any("смерт" in line.lower() for line in lines)


def test_damage_totals_accumulate_in_both_scopes():
    m = SessionMemory()
    m.register(ev("damage_dealt", amount=364, target="Шторм"))
    m.register(ev("damage_dealt", amount=700, target="Tiger II"))
    m.register(ev("damage_received", amount=499, source="Rhm.-B. WT"))
    assert m.damage_dealt == 1064  # сессия
    assert m.battle.damage_dealt == 1064  # текущий бой
    assert m.damage_received == 499
    assert any("урон за бой: 1064" in line for line in m.battle_lines())
    assert any("нанесено урона за сессию: 1064" in line for line in m.session_lines())


def test_battle_scope_resets_session_survives():
    """battle_start обнуляет боевые счётчики, сессионные остаются."""
    m = SessionMemory()
    m.register(ev("damage_dealt", amount=800, target="X"))
    m.register(ev("frag", target="X"))
    m.register(ev("battle_result", outcome="win", silent=True))
    m.register(ev("battle_start", map="Прохоровка", silent=True))
    assert m.battle.damage_dealt == 0
    assert m.battle.frags == 0
    assert m.battle.map == "Прохоровка"
    assert m.damage_dealt == 800  # сессия не сброшена
    assert m.frags == 1
    assert any("боёв за сессию: 1" in line for line in m.session_lines())
    assert not any("урон за бой" in line for line in m.battle_lines())


def test_crit_and_spotted_counters():
    m = SessionMemory()
    for _ in range(3):
        m.register(ev("crit"))
    m.register(ev("spotted"))
    m.register(ev("spotted"))
    assert m.crits == 3
    assert m.spots == 2
    lines = m.battle_lines()
    assert any("критов за бой: 3" in line for line in lines)
    assert any("засветов за бой: 2" in line for line in lines)


def test_assist_and_blocked_accumulate():
    m = SessionMemory()
    m.register(ev("assist", amount=250))
    m.register(ev("assist", amount=800))
    m.register(ev("blocked", amount=400))
    assert m.assist_total == 1050
    assert m.assist_count == 2
    assert m.blocked_total == 400
    assert m.blocked_count == 1
    assert any("ассист за бой: 1050" in line for line in m.battle_lines())
    assert any("ассиста за сессию: 1050" in line for line in m.session_lines())
    assert any("заблокировано бронёй за сессию: 400" in line for line in m.session_lines())


def test_fire_fact_per_battle():
    m = SessionMemory()
    m.register(ev("fire"))
    facts = m.register(ev("fire"))
    assert m.fires == 2
    assert any("2" in f for f in facts)  # горит 2-й раз за бой
    m.register(ev("battle_start", map="X", silent=True))
    facts = m.register(ev("fire"))  # первый пожар нового боя — без факта
    assert facts == []
    assert m.fires == 3  # сессия копит дальше


def test_low_hp_counter():
    m = SessionMemory()
    m.register(ev("low_hp", silent=True))
    m.register(ev("low_hp", silent=True))
    assert m.low_hp_events == 2


def test_damage_milestone_fact():
    m = SessionMemory()
    facts = m.register(ev("damage_milestone", total=2000))
    assert any("2000" in f for f in facts)


def test_battle_start_and_vehicle_change_update_battle_context():
    m = SessionMemory()
    m.register(ev("vehicle_change", tank="T-54"))
    m.register(ev("battle_start", map="Химельсдорф", mode="standard"))
    assert m.battle.map == "Химельсдорф"
    assert m.battle.mode == "standard"
    assert m.battle.tank == "T-54"  # танк из ангара переехал в бой
    lines = m.battle_lines()
    assert any("Химельсдорф" in line for line in lines)
    assert any("T-54" in line for line in lines)


def test_stimulus_expiry():
    s = Stimulus(kind="game_event", type="frag", created_at=time.time() - 30, ttl_s=20)
    assert s.expired()
    fresh = Stimulus(kind="game_event", type="frag")
    assert not fresh.expired()
