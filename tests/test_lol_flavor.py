from stream_director.config import Settings
from stream_director.stimulus import Stimulus
from stream_director.games.lol.flavor import describe_event, flavor_lines
from stream_director.games.lol.module import build_module

LOL_TYPES = ("battle_start", "frag", "death", "assist", "multikill", "first_blood",
             "objective", "turret", "inhib", "ace", "battle_result")


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="lol", payload=payload)


def lol_module():
    return build_module(Settings(), submit=lambda s: None)


def test_describe_covers_all_lol_types():
    for t in LOL_TYPES:
        text = describe_event(game(t))
        assert isinstance(text, str) and text and not text.startswith("Событие:")


def test_fallback_covers_all_lol_types():
    m = lol_module()
    for t in LOL_TYPES:
        line = m.fallback_line(Stimulus(kind="game_event", type=t))
        # battle_result шаблонов не имеет — фолбэк для него молчит (None);
        # у остальных типов должна быть непустая реплика.
        if t == "battle_result":
            assert line is None
        else:
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
    motifs = ("годжо", "сильнейш", "силён", "бессмертн")
    line = lol_module().fallback_line(Stimulus(kind="game_event", type="battle_start"))
    assert any(w in line.lower() for w in motifs)


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
    m = lol_module()
    cases = (
        game("ally_feeding", champion="Yasuo", deaths=8),
        game("ally_carrying", champion="Lee Sin", label="трипл-килл", count=3),
        game("ally_carrying", champion="Lee Sin", kills=9, my_kills=2),
        game("team_gap", kind="spectator", team_kills=6),
        game("team_gap", kind="behind", diff=12),
    )
    for stim in cases:
        line = m.fallback_line(stim)
        assert isinstance(line, str) and line


def test_fallback_objective_sides_differ():
    m = lol_module()
    ours = {m.fallback_line(game("objective", side="ours")) for _ in range(30)}
    theirs = {m.fallback_line(game("objective", side="theirs")) for _ in range(30)}
    assert ours and theirs and ours.isdisjoint(theirs)


def test_joke_angles_wired_into_module():
    m = build_module(Settings(), submit=lambda s: None)
    angles = m.joke_angles()
    assert len(angles) >= 8 and all(isinstance(a, str) for a in angles)


def test_flavor_mentions_ally_targets():
    text = flavor_lines()
    assert "фидер" in text.lower() or "союзник" in text.lower()


def test_templates_are_rich_and_unique():
    templates = lol_module().template_pool.templates
    rich = ("frag", "death", "multikill", "first_blood",
            "turret", "inhib", "ace_ours", "objective_ours", "objective_theirs")
    for key in rich:
        assert len(templates[key]) >= 8, key
    # battle_start/assist/ace_theirs короче остальных — держим планку в 5.
    new = ("battle_start", "assist", "ace_theirs", "ally_feeding",
           "ally_carrying_multikill", "ally_carrying_lead",
           "team_gap_spectator", "team_gap_behind")
    for key in new:
        assert len(templates[key]) >= 5, key
    for key, options in templates.items():
        assert len(set(options)) == len(options), f"дубликаты в {key}"


def test_fallback_no_repeat_last_three():
    m = lol_module()
    picks = [m.fallback_line(game("frag")) for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]


def test_fallback_take_phase_never_repeats_within_session():
    # Пока пул жив, шаблоны не повторяются вовсе; после исчерпания
    # реплики продолжаются (exhausted_pick), молчания нет.
    m = lol_module()
    total = len(m.template_pool.templates["frag"])
    picks = [m.fallback_line(game("frag")) for _ in range(total)]
    assert len(set(picks)) == total  # фаза take: все уникальны
    assert m.fallback_line(game("frag"))  # и дальше не молчим


def test_first_blood_description_names_sides_killer_and_victim():
    # Противник забрал первую кровь, убив союзника — стримера не хороним.
    d = describe_event(game("first_blood", by_me=False, actor="Darius",
                            side="theirs", victim="Lux", victim_me=False))
    assert "противник" in d.lower() and "Darius" in d and "Lux" in d
    assert "жив" in d  # явное «стример жив»
    # Первая кровь за стримером — жертва названа.
    d = describe_event(game("first_blood", by_me=True, actor="Garen",
                            side="ours", victim="Darius", victim_me=False))
    assert "стример" in d.lower() and "Darius" in d
    # Союзник забрал первую кровь — заслуга не стримера.
    d = describe_event(game("first_blood", by_me=False, actor="Lux",
                            side="ours", victim="Darius", victim_me=False))
    assert "союзник" in d.lower() and "Lux" in d and "не стримера" in d
    # Пустой payload не падает (совместимость со старым форматом).
    d = describe_event(game("first_blood"))
    assert isinstance(d, str) and d


def test_assist_description_names_ally_killer():
    d = describe_event(game("assist", target="Darius", killer="Lux"))
    assert "Lux" in d and "Darius" in d
    d = describe_event(game("assist"))  # без killer — не падаем
    assert isinstance(d, str) and d


def test_flavor_forbids_kill_misattribution():
    text = flavor_lines()
    assert "кто убил" in text.lower() or "не путай" in text.lower()
