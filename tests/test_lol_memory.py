from wot_ai_commentator.events import Stimulus
from wot_ai_commentator.games.lol.memory import LolSessionMemory


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol", payload=payload)


def test_battle_start_resets_battle_and_sets_champion():
    m = LolSessionMemory()
    m.register(game("frag", target="Darius"))
    m.register(game("battle_start", map="Map11", mode="CLASSIC",
                    champion="Garen", silent=True))
    assert m.battle.kills == 0
    assert m.battle.champion == "Garen"
    assert any("Garen" in line for line in m.battle_lines())


def test_kda_and_score_line():
    m = LolSessionMemory()
    m.register(game("frag", target="Darius"))
    m.register(game("death", killer="Darius"))
    m.register(game("assist", target="Darius"))
    assert m.battle.kills == 1 and m.battle.deaths == 1 and m.battle.assists == 1
    assert any("1/1/1" in line for line in m.battle_lines())


def test_repeat_killer_fact():
    m = LolSessionMemory()
    m.register(game("death", killer="Darius"))
    facts = m.register(game("death", killer="Darius"))
    assert any("2-я смерть" in f and "Darius" in f for f in facts)


def test_penta_fact_and_session_count():
    m = LolSessionMemory()
    facts = m.register(game("multikill", count=5, label="пентакилл"))
    assert any("ПЕНТАКИЛЛ" in f for f in facts)
    assert any("пентакилл" in line for line in m.session_lines())


def test_objectives_ours_counted():
    m = LolSessionMemory()
    m.register(game("objective", kind="дракон (Fire)", side="ours", stolen=False))
    m.register(game("objective", kind="барон", side="theirs", stolen=False))
    assert any("дракон" in line for line in m.battle_lines())


def test_session_wins_and_games():
    m = LolSessionMemory()
    m.register(game("battle_result", outcome="win", silent=True))
    m.register(game("battle_result", outcome="loss", silent=True))
    assert any("игр за сессию: 2, побед: 1" in line for line in m.session_lines())


def test_summary_is_battle_plus_session():
    m = LolSessionMemory()
    m.register(game("frag", target="Darius"))
    m.register(game("battle_result", outcome="win", silent=True))
    assert m.summary_lines() == m.battle_lines() + m.session_lines()
