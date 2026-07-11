from stream_director.config import Settings
from stream_director.stimulus import Stimulus
from stream_director.games.lol.flavor import describe_event, fallback_line, flavor_lines
from stream_director.games.lol.module import build_module

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
    unknown = describe_event(game("objective", kind="дракон", side="unknown"))
    assert "стримера" in ours and "барон" in ours
    assert "Противник" in theirs and "УКРАДЕН" in theirs
    # unknown — нейтрально, без ложного «противник забрал»
    assert "Противник" not in unknown and "дракон" in unknown


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


def test_ally_event_descriptions():
    d = describe_event(game("ally_feeding", champion="Yasuo", deaths=8))
    assert "Yasuo" in d and "8" in d and "кормильц" in d.lower()
    d = describe_event(game("ally_carrying", champion="Lee Sin",
                            label="трипл-килл", count=3))
    assert "Lee Sin" in d and "трипл-килл" in d
    d = describe_event(game("ally_carrying", champion="Lee Sin",
                            kills=9, my_kills=2))
    assert "9" in d and "стример" in d.lower()
    d = describe_event(game("team_gap", kind="spectator", team_kills=6))
    assert "0/0/0" in d
    d = describe_event(game("team_gap", kind="behind", diff=12))
    assert "12" in d


def test_fallback_covers_new_ally_types():
    cases = (
        game("ally_feeding", champion="Yasuo", deaths=8),
        game("ally_carrying", champion="Lee Sin", label="трипл-килл", count=3),
        game("ally_carrying", champion="Lee Sin", kills=9, my_kills=2),
        game("team_gap", kind="spectator", team_kills=6),
        game("team_gap", kind="behind", diff=12),
    )
    for stim in cases:
        line = fallback_line(stim)
        assert isinstance(line, str) and line and line != "Без комментариев."


def test_fallback_objective_sides_differ():
    ours = {fallback_line(game("objective", side="ours")) for _ in range(30)}
    theirs = {fallback_line(game("objective", side="theirs")) for _ in range(30)}
    assert ours and theirs and ours.isdisjoint(theirs)


def test_joke_angles_wired_into_module():
    m = build_module(Settings(), submit=lambda s: None)
    angles = m.joke_angles()
    assert len(angles) >= 8 and all(isinstance(a, str) for a in angles)


def test_flavor_mentions_ally_targets():
    text = flavor_lines()
    assert "фидер" in text.lower() or "союзник" in text.lower()


def test_templates_are_rich_and_unique():
    from stream_director.games.lol.flavor import _TEMPLATES
    rich = ("battle_start", "frag", "death", "assist", "multikill", "first_blood",
            "turret", "inhib", "ace_ours", "objective_ours", "objective_theirs")
    for key in rich:
        assert len(_TEMPLATES[key]) >= 8, key
    new = ("ally_feeding", "ally_carrying_multikill", "ally_carrying_lead",
           "team_gap_spectator", "team_gap_behind", "ace_theirs")
    for key in new:
        assert len(_TEMPLATES[key]) >= 5, key
    for key, options in _TEMPLATES.items():
        assert len(set(options)) == len(options), f"дубликаты в {key}"


def test_fallback_no_repeat_last_three():
    picks = [fallback_line(game("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]
