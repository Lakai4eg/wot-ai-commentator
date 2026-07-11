from stream_director.stimulus import Stimulus
from stream_director.games.lol.memory import LolSessionMemory


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


def test_team_state_notable_allies_in_lines():
    mem = LolSessionMemory()
    mem.register(game("battle_start", champion="Garen"))
    mem.register(game("team_state", silent=True, allies=[
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
    mem.register(game("battle_start", champion="Garen"))
    mem.register(game("team_state", silent=True, allies=[
        {"champion": f"Champ{i}", "kills": 0, "deaths": 6} for i in range(4)
    ]))
    ally_lines = [line for line in mem.battle_lines() if line.startswith("союзник")]
    assert len(ally_lines) == 2


def test_allies_reset_on_battle_start():
    mem = LolSessionMemory()
    mem.register(game("team_state", silent=True,
                      allies=[{"champion": "Yasuo", "kills": 0, "deaths": 9}]))
    mem.register(game("battle_start", champion="Garen"))
    assert not any("Yasuo" in line for line in mem.battle_lines())
