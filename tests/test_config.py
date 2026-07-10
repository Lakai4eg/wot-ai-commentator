import json

from stream_director.config import Settings, load_settings, save_settings


def test_load_missing_file_returns_defaults(tmp_path):
    s = load_settings(tmp_path / "nope.json")
    assert s == Settings()


def test_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(gemini_api_key="k", twitch_channel="chan", global_cooldown_s=7.0)
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded == s


def test_unknown_keys_ignored_and_missing_defaulted(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"twitch_channel": "x", "bogus_key": 1}), encoding="utf-8")
    s = load_settings(path)
    assert s.twitch_channel == "x"
    assert s.gemini_model == "gemini-3.1-flash-lite"


def test_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_new_defaults_after_ocr_removal():
    s = Settings()
    assert s.wotstat_url == "ws://localhost:38200"
    assert s.global_cooldown_s == 4.0
    # OCR-поля удалены полностью.
    fields = set(s.__dataclass_fields__)
    assert not fields & {
        "capture_fps",
        "zone_diff_threshold",
        "monitor_index",
        "game_window_hint",
        "max_replicas_per_minute",
    }


def test_lol_url_default():
    assert Settings().lol_url == "https://127.0.0.1:2999"


def test_voice_defaults():
    s = Settings()
    assert s.default_voice == "baya"
    assert s.voice_by_priority == {}
    assert s.voice_overrides == {}


def test_voice_fields_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(
        default_voice="xenia",
        voice_by_priority={"high": "aidar", "critical": "kseniya"},
        voice_overrides={"death": "eugene"},
    )
    save_settings(s, path)
    assert load_settings(path) == s
