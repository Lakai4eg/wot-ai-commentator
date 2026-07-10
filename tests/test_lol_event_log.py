import json

from stream_director.games.lol.event_log import LolEventLog, NullEventLog


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_start_game_writes_meta_and_events(tmp_path):
    logf = LolEventLog(dir_path=tmp_path, clock=lambda: 1_000_000.0)
    logf.start_game({"map": "Map11", "mode": "CLASSIC", "champion": "Garen"})
    logf.log_event({"EventID": 0, "EventName": "GameStart"})
    logf.log_event({"EventID": 7, "EventName": "DragonKill", "Stolen": "True"})
    logf.close()

    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = _lines(files[0])
    assert rows[0]["kind"] == "game_start" and rows[0]["champion"] == "Garen"
    assert [r["EventName"] for r in rows[1:]] == ["GameStart", "DragonKill"]
    assert rows[2]["raw"]["Stolen"] == "True"  # сырое событие целиком


def test_log_event_before_start_is_ignored(tmp_path):
    logf = LolEventLog(dir_path=tmp_path)
    logf.log_event({"EventID": 1, "EventName": "ChampionKill"})  # файла ещё нет
    assert list(tmp_path.glob("*.jsonl")) == []


def test_new_game_rotates_to_new_file(tmp_path):
    logf = LolEventLog(dir_path=tmp_path, clock=lambda: 1_000_000.0)
    logf.start_game({"champion": "Ashe"})
    logf.log_event({"EventID": 0, "EventName": "GameStart"})
    logf.start_game({"champion": "Jinx"})  # новый матч — новый файл
    logf.log_event({"EventID": 0, "EventName": "GameStart"})
    logf.close()

    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 2  # два матча — два файла
    champs = {_lines(f)[0]["champion"] for f in files}
    assert champs == {"Ashe", "Jinx"}


def test_null_event_log_is_noop(tmp_path):
    logf = NullEventLog()
    logf.start_game({"champion": "Garen"})
    logf.log_event({"EventID": 0, "EventName": "GameStart"})
    logf.close()
    assert list(tmp_path.glob("*")) == []
