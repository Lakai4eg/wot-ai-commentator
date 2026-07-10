from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Stimulus
from wot_ai_commentator.games.lol.flavor import describe_event, fallback_line, flavor_lines
from wot_ai_commentator.games.lol.module import build_module

LOL_TYPES = ("frag", "death", "assist", "multikill", "first_blood",
             "objective", "turret", "inhib", "ace", "battle_result")


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol", payload=payload)


def test_describe_covers_all_lol_types():
    for t in LOL_TYPES:
        text = describe_event(game(t))
        assert isinstance(text, str) and text and not text.startswith("Событие:")


def test_fallback_covers_all_lol_types():
    for t in LOL_TYPES:
        line = fallback_line(Stimulus(kind="game_event", type=t))
        assert isinstance(line, str) and line


def test_objective_sides_and_steal_note():
    ours = describe_event(game("objective", kind="барон", side="ours", stolen=False))
    theirs = describe_event(game("objective", kind="дракон (Fire)", side="theirs", stolen=True))
    assert "стримера" in ours and "барон" in ours
    assert "Противник" in theirs and "УКРАДЕН" in theirs


def test_flavor_mentions_lol():
    assert "League of Legends" in flavor_lines()


def test_build_module_contract():
    m = build_module(Settings(), submit=lambda s: None)
    assert m.id == "lol"
    assert m.always_speak_types == frozenset({"death", "multikill"})
    assert isinstance(m.diag(), dict)
