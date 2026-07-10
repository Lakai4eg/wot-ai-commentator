# tests/test_lol_mapper.py
import time

from wot_ai_commentator.events import Priority
from wot_ai_commentator.games.lol.mapper import LolMapper

ME = "Streamer#RU1"
ENEMY = "Enemy#EU1"
ALLY = "Ally#RU2"


def payload(events=(), game_time=100.0, hp=(1000.0, 1000.0), me_dead=False):
    return {
        "activePlayer": {
            "riotId": ME,
            "championStats": {"currentHealth": hp[0], "maxHealth": hp[1]},
        },
        "allPlayers": [
            {"riotId": ME, "championName": "Garen", "team": "ORDER",
             "isDead": me_dead, "scores": {"kills": 0, "deaths": 0, "assists": 0}},
            {"riotId": ALLY, "championName": "Lux", "team": "ORDER",
             "isDead": False, "scores": {}},
            {"riotId": ENEMY, "championName": "Darius", "team": "CHAOS",
             "isDead": False, "scores": {}},
        ],
        "events": {"Events": list(events)},
        "gameData": {"gameMode": "CLASSIC", "mapName": "Map11", "gameTime": game_time},
    }


def make():
    stims = []
    return LolMapper(submit=stims.append), stims


def test_fresh_game_emits_silent_battle_start():
    m, stims = make()
    m.handle_payload(payload(
        events=[{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}],
        game_time=5.0,
    ))
    assert [s.type for s in stims] == ["battle_start"]
    s = stims[0]
    assert s.game == "lol" and s.payload["silent"] is True
    assert s.payload["champion"] == "Garen"


def test_midgame_connect_fast_forwards_history():
    m, stims = make()
    old = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY},
    ]
    m.handle_payload(payload(events=old, game_time=600.0))
    # История не переигрывается — только тихий battle_start для памяти.
    assert [s.type for s in stims] == ["battle_start"]
    # …но НОВЫЕ события после подключения обрабатываются.
    new = old + [{"EventID": 2, "EventName": "ChampionKill",
                  "KillerName": ME, "VictimName": ENEMY}]
    m.handle_payload(payload(events=new, game_time=605.0))
    assert [s.type for s in stims] == ["battle_start", "frag"]


def test_kill_death_assist_multikill():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY},
        {"EventID": 2, "EventName": "ChampionKill", "KillerName": ENEMY, "VictimName": ME},
        {"EventID": 3, "EventName": "ChampionKill", "KillerName": ALLY,
         "VictimName": ENEMY, "Assisters": [ME]},
        {"EventID": 4, "EventName": "Multikill", "KillerName": ME, "KillStreak": 5},
    ]
    m.handle_payload(payload(events=events, game_time=30.0))
    types = [s.type for s in stims]
    assert types == ["battle_start", "frag", "death", "assist", "multikill"]
    frag, death, assist, multi = stims[1], stims[2], stims[3], stims[4]
    assert frag.payload["target"] == "Darius" and frag.priority == Priority.HIGH
    assert death.payload["killer"] == "Darius" and death.priority == Priority.HIGH
    assert assist.payload["target"] == "Darius" and assist.priority == Priority.LOW
    assert multi.payload["label"] == "пентакилл" and multi.priority == Priority.CRITICAL


def test_objectives_sides_and_steal():
    m, stims = make()
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "DragonKill", "KillerName": ALLY,
         "DragonType": "Fire", "Stolen": "False"},
        {"EventID": 2, "EventName": "BaronKill", "KillerName": ENEMY, "Stolen": "True"},
        {"EventID": 3, "EventName": "TurretKilled", "KillerName": ME},
        {"EventID": 4, "EventName": "Ace", "AcingTeam": "CHAOS"},
        {"EventID": 5, "EventName": "GameEnd", "Result": "Win"},
    ]
    m.handle_payload(payload(events=events, game_time=10.0))
    by_type = {s.type: s for s in stims}
    dragon = [s for s in stims if s.type == "objective"][0]
    baron = [s for s in stims if s.type == "objective"][1]
    assert dragon.payload["side"] == "ours" and "дракон" in dragon.payload["kind"]
    assert baron.payload["side"] == "theirs" and baron.payload["stolen"] is True
    assert baron.priority == Priority.HIGH
    assert by_type["turret"].type == "turret"
    assert by_type["ace"].payload["side"] == "theirs"
    assert by_type["battle_result"].payload == {"outcome": "win", "silent": True}


def test_new_match_resets_cursor():
    m, stims = make()
    m.handle_payload(payload(
        events=[{"EventID": 0, "EventName": "GameStart"},
                {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY}],
        game_time=900.0,
    ))
    stims.clear()
    # gameTime пошёл назад — новый матч, GameStart с EventID 0 снова живой.
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=3.0))
    assert [s.type for s in stims] == ["battle_start"]


def test_partial_payload_without_gamedata_is_skipped():
    m, stims = make()
    events = [{"EventID": 0, "EventName": "GameStart"},
              {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY}]
    m.handle_payload(payload(events=events, game_time=100.0))
    stims.clear()
    # Частичный payload без gameData: не gameTime=0 → не «время пошло назад».
    partial = payload(events=events, game_time=100.0)
    del partial["gameData"]
    m.handle_payload(partial)
    assert stims == []  # ничего не переиграно и не сброшено
    # Следующий нормальный снапшот — без ложного battle_start и без реплея.
    m.handle_payload(payload(events=events, game_time=101.0))
    assert stims == []


def test_midgame_connect_while_dead_does_not_replay_death():
    m, stims = make()
    # Подключились посреди матча, стример прямо сейчас мёртв: эта смерть —
    # история, её не переигрываем (спека §3.2/§4.5).
    old = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ENEMY, "VictimName": ME},
    ]
    m.handle_payload(payload(events=old, game_time=600.0, me_dead=True))
    assert [s.type for s in stims] == ["battle_start"]
    # Следующая НАСТОЯЩАЯ смерть после подключения озвучивается.
    m.handle_payload(payload(events=old, game_time=605.0))  # респаун
    new = old + [{"EventID": 2, "EventName": "ChampionKill",
                  "KillerName": ENEMY, "VictimName": ME}]
    m.handle_payload(payload(events=new, game_time=610.0, me_dead=True))
    assert [s.type for s in stims] == ["battle_start", "death"]


def test_two_distinct_journal_deaths_in_one_batch_both_emitted():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    # Полл отставал: журнал догнал сразу двумя РАЗНЫМИ смертями (разные EventID).
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ENEMY,
         "VictimName": ME, "EventTime": 20.0},
        {"EventID": 2, "EventName": "ChampionKill", "KillerName": ENEMY,
         "VictimName": ME, "EventTime": 55.0},
    ]
    m.handle_payload(payload(events=events, game_time=60.0, me_dead=True))
    deaths = [s for s in stims if s.type == "death"]
    assert len(deaths) == 2
    assert all(d.payload["killer"] == "Darius" for d in deaths)


def test_journal_death_deduped_after_isdead_safety_net():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    # Страховка сработала первой: isDead=true, журнал отстал.
    m.handle_payload(payload(game_time=50.0, me_dead=True))
    # Журнал догнал: тот же ChampionKill не должен родить вторую смерть.
    events = [{"EventID": 0, "EventName": "GameStart"},
              {"EventID": 1, "EventName": "ChampionKill", "KillerName": ENEMY, "VictimName": ME}]
    m.handle_payload(payload(events=events, game_time=51.0, me_dead=True))
    deaths = [s for s in stims if s.type == "death"]
    assert len(deaths) == 1 and deaths[0].payload["killer"] == "неизвестный"


def test_new_match_detected_by_journal_reset_without_time_backwards():
    m, stims = make()
    # Матч 1 умер через 10 секунд (порт пропал), курсор дошёл до EventID 1.
    m.handle_payload(payload(
        events=[{"EventID": 0, "EventName": "GameStart"},
                {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY}],
        game_time=10.0,
    ))
    stims.clear()
    # Матч 2 впервые виден на gameTime=15 (> 10 — «назад» не сработает),
    # но журнал начался заново с GameStart — курсор обязан сброситься.
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=15.0))
    assert [s.type for s in stims] == ["battle_start"]


def test_isdead_safety_net_no_duplicates():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m._death_emitted_at = time.time() - 60  # давно не умирали
    m.handle_payload(payload(game_time=50.0, me_dead=True))
    m.handle_payload(payload(game_time=51.0, me_dead=True))  # всё ещё мёртв — без дубля
    deaths = [s for s in stims if s.type == "death"]
    assert len(deaths) == 1 and deaths[0].payload["killer"] == "неизвестный"


def test_low_hp_silent_once_per_life():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m.handle_payload(payload(game_time=50.0, hp=(100.0, 1000.0)))
    m.handle_payload(payload(game_time=51.0, hp=(90.0, 1000.0)))
    low = [s for s in stims if s.type == "low_hp"]
    assert len(low) == 1 and low[0].payload["silent"] is True
