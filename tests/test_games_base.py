from wot_ai_commentator.events import Stimulus
from wot_ai_commentator.games.base import ActiveGameTracker, GameModule


def test_stimulus_game_defaults_empty():
    s = Stimulus(kind="game_event", type="frag")
    assert s.game == ""


def test_tracker_defaults_to_wot():
    t = ActiveGameTracker()
    assert t.active == "wot"


def test_tracker_switches_on_live_and_sticks():
    t = ActiveGameTracker()
    t.mark_live("lol")
    assert t.active == "lol"
    # Порт LoL умер между матчами — активная игра НЕ меняется…
    assert t.active == "lol"
    # …пока не оживёт другая.
    t.mark_live("wot")
    assert t.active == "wot"


def test_game_module_holds_contract():
    m = GameModule(
        id="wot",
        display_name="Мир танков",
        source=object(),
        memory=object(),
        describe_event=lambda s: "событие",
        flavor_lines=lambda: "колорит",
        fallback_line=lambda s: None,
        always_speak_types=frozenset({"death"}),
        diag=lambda: {},
    )
    assert m.id == "wot"
    assert "death" in m.always_speak_types
