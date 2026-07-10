from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Priority, Stimulus
from wot_ai_commentator.games.wot.flavor import describe_event, fallback_line, flavor_lines
from wot_ai_commentator.games.wot.module import build_module

WOT_TYPES = (
    "frag", "death", "ammo_rack", "oneshot", "damage_record", "battle_result",
    "damage_dealt", "damage_received", "crit", "spotted", "tier11", "assist",
    "blocked", "fire", "damage_milestone", "base_capture",
)


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="wot",
                    priority=Priority.NORMAL, payload=payload)


def test_describe_covers_all_wot_types():
    for t in WOT_TYPES:
        text = describe_event(game(t))
        assert isinstance(text, str) and text and not text.startswith("Событие:")


def test_fallback_covers_all_wot_types():
    for t in WOT_TYPES:
        line = fallback_line(Stimulus(kind="game_event", type=t))
        assert isinstance(line, str) and line


def test_arta_note_in_description():
    text = describe_event(game("damage_received", amount=500, source="G.W.", from_arta=True))
    assert "АРТЫ" in text


def test_flavor_mentions_tanks():
    assert "танк" in flavor_lines().lower() or "Мир танков" in flavor_lines()


def test_build_module_contract():
    m = build_module(Settings(), submit=lambda s: None)
    assert m.id == "wot"
    assert m.always_speak_types == frozenset({"death"})
    assert m.source is not None and m.memory is not None
    assert callable(m.describe_event) and callable(m.fallback_line)
    assert isinstance(m.diag(), dict)
