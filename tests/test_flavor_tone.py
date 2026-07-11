"""Тон флейвора модулей: roast без смягчителей и без ИИ-идентичности."""

from stream_director.games.lol import flavor as lol_flavor
from stream_director.games.wot import flavor as wot_flavor

# Формулировки старой «доброй» персоны — их не должно остаться в промптах.
SOFT_MARKERS = (
    "по-доброму",
    "без злобы",
    "ИИ без рук",
    "смеёмся вместе",
    "Дружески",
    "похвали",
    "союзника хвали",
)


def test_lol_flavor_and_descriptions_have_no_soft_tone():
    corpus = lol_flavor.flavor_lines() + " ".join(
        lol_flavor._EVENT_DESCRIPTIONS.values()
    )
    for marker in SOFT_MARKERS:
        assert marker not in corpus, marker


def test_lol_joke_angles_have_no_ai_identity():
    assert all("ИИ" not in angle for angle in lol_flavor.joke_angles())


def test_wot_flavor_has_no_ai_identity_and_no_fixed_main_target():
    f = wot_flavor.flavor_lines()
    assert "ИИ без рук" not in f
    assert "стример — главная" not in f
    for marker in SOFT_MARKERS:
        assert marker not in f, marker
