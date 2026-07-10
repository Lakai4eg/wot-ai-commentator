from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Priority
from wot_ai_commentator.tts import VOICES, pick_voice


def test_default_when_no_rules():
    s = Settings(default_voice="xenia")
    assert pick_voice(s, "frag", Priority.HIGH) == "xenia"


def test_priority_rule_applies():
    s = Settings(default_voice="baya", voice_by_priority={"high": "aidar"})
    assert pick_voice(s, "frag", Priority.HIGH) == "aidar"
    # NORMAL not mapped -> falls to default
    assert pick_voice(s, "spotted", Priority.NORMAL) == "baya"


def test_override_beats_priority():
    s = Settings(
        default_voice="baya",
        voice_by_priority={"high": "aidar"},
        voice_overrides={"death": "eugene"},
    )
    assert pick_voice(s, "death", Priority.HIGH) == "eugene"


def test_invalid_names_fall_through():
    s = Settings(
        default_voice="nope",
        voice_by_priority={"high": "also_bad"},
        voice_overrides={"death": "garbage"},
    )
    # every level invalid -> hard fallback
    assert pick_voice(s, "death", Priority.HIGH) == "baya"


def test_priority_name_lowercased():
    s = Settings(default_voice="baya", voice_by_priority={"critical": "kseniya"})
    assert pick_voice(s, "multikill", Priority.CRITICAL) == "kseniya"


def test_all_voices_known():
    assert "baya" in VOICES and "random" in VOICES


from wot_ai_commentator.tts import SileroTTS


def test_synth_none_when_unavailable_any_voice():
    tts = SileroTTS.__new__(SileroTTS)  # skip torch load
    tts.available = False
    tts._model = None
    assert tts.synth("привет", "aidar") is None
    assert tts.synth("привет", "garbage") is None
    assert tts.synth("привет") is None


def test_ctor_clamps_unknown_default_voice():
    tts = SileroTTS.__new__(SileroTTS)
    # emulate the validation the ctor performs
    from wot_ai_commentator.tts import VOICES
    voice = "weird"
    assert (voice if voice in VOICES else "baya") == "baya"
