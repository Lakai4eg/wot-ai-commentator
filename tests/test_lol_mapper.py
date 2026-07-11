# tests/test_lol_mapper.py
import time

from stream_director.stimulus import Priority
from stream_director.games.lol.mapper import LolMapper

ME = "Streamer#RU1"
ENEMY = "Enemy#EU1"
ALLY = "Ally#RU2"


def payload(events=(), game_time=100.0, hp=(1000.0, 1000.0), me_dead=False, deaths=0):
    return {
        "activePlayer": {
            "riotId": ME,
            "championStats": {"currentHealth": hp[0], "maxHealth": hp[1]},
        },
        "allPlayers": [
            {"riotId": ME, "championName": "Garen", "team": "ORDER",
             "isDead": me_dead, "scores": {"kills": 0, "deaths": deaths, "assists": 0}},
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


class RecLog:
    """Фейковый журнал: запоминает старты матчей и имена событий."""

    def __init__(self):
        self.games = []
        self.events = []

    def start_game(self, meta):
        self.games.append(meta)

    def log_event(self, ev):
        self.events.append(ev.get("EventName"))

    def close(self):
        pass


def test_event_log_records_all_journal_events():
    rec = RecLog()
    stims = []
    m = LolMapper(submit=stims.append, event_log=rec)
    m.handle_payload(payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME, "VictimName": ENEMY},
        {"EventID": 2, "EventName": "SomeUnknownEvent"},  # необработанное — тоже пишем
    ], game_time=5.0))
    # Логируем каждое свежее событие журнала, включая неизвестные мапперу.
    assert rec.events == ["GameStart", "ChampionKill", "SomeUnknownEvent"]
    assert len(rec.games) == 1 and rec.games[0]["champion"] == "Garen"


def test_event_log_rotates_on_new_match():
    rec = RecLog()
    stims = []
    m = LolMapper(submit=stims.append, event_log=rec)
    # Подключение посреди матча — старт матча зафиксирован, историю не логируем.
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=900.0))
    assert len(rec.games) == 1 and rec.events == []
    # gameTime пошёл назад — новый матч, новый файл журнала.
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=3.0))
    assert len(rec.games) == 2


def test_fresh_game_emits_speaking_battle_start():
    m, stims = make()
    m.handle_payload(payload(
        events=[{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}],
        game_time=5.0,
    ))
    assert [s.type for s in stims] == ["battle_start"]
    s = stims[0]
    # Настоящий GameStart звучит (интро), не тихий.
    assert s.game == "lol" and s.payload["silent"] is False
    assert s.payload["champion"] == "Garen"


def test_midgame_connect_battle_start_stays_silent():
    m, stims = make()
    # Подключились посреди матча — «прибытие» уже не к месту, старт тихий.
    m.handle_payload(payload(
        events=[{"EventID": 0, "EventName": "GameStart"}],
        game_time=600.0,
    ))
    assert [s.type for s in stims] == ["battle_start"]
    assert stims[0].payload["silent"] is True


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
    assert dragon.payload["kind_key"] == "dragon"
    assert baron.payload["kind_key"] == "baron"
    assert baron.payload["side"] == "theirs" and baron.payload["stolen"] is True
    assert baron.priority == Priority.HIGH
    assert by_type["turret"].type == "turret"
    assert by_type["ace"].payload["side"] == "theirs"
    assert by_type["battle_result"].payload == {"outcome": "win", "silent": True}


def test_events_without_riot_tag_still_attributed():
    # Регрессия: журнал шлёт имя без Riot-тэга («Streamer» вместо «Streamer#RU1»),
    # что не совпадало с riotId в allPlayers — фраг молчал, дракон уходил «врагу».
    m, stims = make()
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        # киллер без тэга и в другом регистре — всё равно это стример
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": "streamer",
         "VictimName": "Enemy"},
        {"EventID": 2, "EventName": "DragonKill", "KillerName": "Streamer",
         "DragonType": "Fire"},
    ]
    m.handle_payload(payload(events=events, game_time=10.0))
    by_type = {s.type: s for s in stims}
    assert by_type["frag"].payload["target"] == "Darius"       # килл озвучен
    assert by_type["objective"].payload["side"] == "ours"      # дракон — наш


def test_objective_by_unknown_killer_is_not_blamed_on_enemy():
    m, stims = make()
    events = [
        {"EventID": 0, "EventName": "GameStart"},
        # киллер, которого нет в allPlayers — сторону честно не знаем
        {"EventID": 1, "EventName": "BaronKill", "KillerName": "SomeoneElse#XX1"},
    ]
    m.handle_payload(payload(events=events, game_time=10.0))
    obj = [s for s in stims if s.type == "objective"][0]
    assert obj.payload["side"] == "unknown"  # не «theirs»


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


def test_journal_death_deduped_after_counter_safety_net():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    # Страховка сработала первой: счётчик смертей вырос, журнал отстал.
    m.handle_payload(payload(game_time=50.0, me_dead=True, deaths=1))
    # Журнал догнал: тот же ChampionKill не должен родить вторую смерть.
    events = [{"EventID": 0, "EventName": "GameStart"},
              {"EventID": 1, "EventName": "ChampionKill", "KillerName": ENEMY, "VictimName": ME}]
    m.handle_payload(payload(events=events, game_time=51.0, me_dead=True, deaths=1))
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


def test_death_counter_safety_net_no_duplicates():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m._death_emitted_at = time.time() - 60  # давно не умирали
    m.handle_payload(payload(game_time=50.0, me_dead=True, deaths=1))   # счётчик 0→1
    m.handle_payload(payload(game_time=51.0, me_dead=True, deaths=1))   # всё ещё 1 — без дубля
    deaths = [s for s in stims if s.type == "death"]
    assert len(deaths) == 1 and deaths[0].payload["killer"] == "неизвестный"


def test_isdead_true_without_death_counter_growth_is_not_a_death():
    # Регрессия (баг с ложной смертью при первой крови): isDead=True как
    # артефакт practice tool / чужой смерти, но счётчик смертей стримера не
    # вырос — журнал смерти не видел, озвучивать нечего.
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m.handle_payload(payload(game_time=50.0, me_dead=True, deaths=0))
    assert [s for s in stims if s.type == "death"] == []


def test_death_counter_catches_non_champion_death():
    # Смерть не от чемпиона (башня/миньон/казнь): ChampionKill в журнале нет,
    # но счётчик смертей вырос — озвучиваем как смерть с неизвестным убийцей.
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m.handle_payload(payload(game_time=50.0, me_dead=True, deaths=1))
    d = [s for s in stims if s.type == "death"]
    assert len(d) == 1 and d[0].payload["killer"] == "неизвестный"


def test_low_hp_silent_once_per_life():
    m, stims = make()
    m.handle_payload(payload(events=[{"EventID": 0, "EventName": "GameStart"}], game_time=5.0))
    stims.clear()
    m.handle_payload(payload(game_time=50.0, hp=(100.0, 1000.0)))
    m.handle_payload(payload(game_time=51.0, hp=(90.0, 1000.0)))
    low = [s for s in stims if s.type == "low_hp"]
    assert len(low) == 1 and low[0].payload["silent"] is True


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


def test_first_blood_carries_killer_victim_and_side():
    """Первая кровь несёт сторону и жертву: ЛЛМ не должна хоронить стримера,
    когда противник убил союзника."""
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ENEMY,
         "VictimName": ALLY, "Assisters": []},
        {"EventID": 2, "EventName": "FirstBlood", "Recipient": ENEMY},
    ], game_time=5.0))
    fb = [s for s in stims if s.type == "first_blood"]
    assert len(fb) == 1
    p = fb[0].payload
    assert p["by_me"] is False
    assert p["side"] == "theirs"
    assert p["actor"] == "Darius"
    assert p["victim"] == "Lux"
    assert p["victim_me"] is False


def test_first_blood_by_me_names_victim():
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ME,
         "VictimName": ENEMY, "Assisters": []},
        {"EventID": 2, "EventName": "FirstBlood", "Recipient": ME},
    ], game_time=5.0))
    p = [s for s in stims if s.type == "first_blood"][0].payload
    assert p["by_me"] is True and p["side"] == "ours"
    assert p["victim"] == "Darius" and p["victim_me"] is False


def test_first_blood_without_matching_kill_has_no_victim():
    # Recipient не совпал с последним киллом — жертву не выдумываем.
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "FirstBlood", "Recipient": ENEMY},
    ], game_time=5.0))
    p = [s for s in stims if s.type == "first_blood"][0].payload
    assert p["victim"] is None and p["victim_me"] is False


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


def test_assist_carries_ally_killer():
    m, stims = make()
    m.handle_payload(team_payload(events=[
        {"EventID": 0, "EventName": "GameStart"},
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": ALLY,
         "VictimName": ENEMY, "Assisters": [ME]},
    ], game_time=5.0))
    a = [s for s in stims if s.type == "assist"][0]
    assert a.payload["target"] == "Darius"
    assert a.payload["killer"] == "Lux"
