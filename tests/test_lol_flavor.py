from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Stimulus
from wot_ai_commentator.games.lol.flavor import describe_event, fallback_line, flavor_lines
from wot_ai_commentator.games.lol.module import build_module

LOL_TYPES = ("battle_start", "frag", "death", "assist", "multikill", "first_blood",
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


def test_battle_start_carries_gojo_bit():
    # Интро на старте матча — про прибытие Годжо на Ущелье призывателей.
    desc = describe_event(game("battle_start", champion="Yasuo"))
    assert "Годжо" in desc and "Yasuo" in desc
    # И фолбэк (когда LLM мертва) тоже держит эту шутку — любой из мотивов Годжо.
    motifs = ("Годжо", "сильнейший", "Бесконечность", "Шесть Глаз")
    line = fallback_line(Stimulus(kind="game_event", type="battle_start"))
    assert any(w in line for w in motifs)


def test_build_module_contract():
    m = build_module(Settings(), submit=lambda s: None)
    assert m.id == "lol"
    assert m.always_speak_types == frozenset({"battle_start", "death", "multikill"})
    assert isinstance(m.diag(), dict)
