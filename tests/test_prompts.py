from wot_ai_commentator.commentary.prompts import build_prompt
from wot_ai_commentator.commentary.templates import fallback_line
from wot_ai_commentator.events import GAME_EVENT_TYPES, Priority, Stimulus


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, priority=Priority.NORMAL, payload=payload)


def order(text):
    return Stimulus(
        kind="chat_order", type="dir", priority=Priority.NORMAL,
        payload={"text": text, "username": "viewer1"},
    )


def test_prompt_contains_event_and_facts():
    p = build_prompt(game("ammo_rack"), ["это уже 2-я боеукладка"])
    assert "боеукладк" in p.lower()
    assert "2-я боеукладка" in p


def test_chat_order_wrapped_and_isolated():
    p = build_prompt(order("похвали стримера"), [])
    assert "<заказ>похвали стримера</заказ>" in p
    assert "не инструкции" in p.lower() or "не команды" in p.lower()


def test_chat_order_truncated():
    long_text = "а" * 500
    p = build_prompt(order(long_text), [])
    start = p.index("<заказ>") + len("<заказ>")
    end = p.index("</заказ>")
    assert end - start <= 200


def test_fallback_exists_for_all_game_types():
    for t in GAME_EVENT_TYPES:
        line = fallback_line(Stimulus(kind="game_event", type=t))
        assert isinstance(line, str) and line


def test_base_capture_is_team_event_not_player():
    """Захват ведёт команда — промпт не должен приписывать его стримеру."""
    ours = build_prompt(game("base_capture", side="ours"), [])
    theirs = build_prompt(game("base_capture", side="theirs"), [])
    assert "союзники" in ours.lower()
    assert "противник" in theirs.lower()
    assert "не приписывай" in ours.lower()


def test_base_capture_fallback_respects_side():
    ours = fallback_line(game("base_capture", side="ours"))
    theirs = fallback_line(game("base_capture", side="theirs"))
    assert "наш" in ours.lower() or "союзник" in ours.lower()
    assert "нашу базу" in theirs.lower() or "нашей базе" in theirs.lower()


def test_arta_hit_gets_snarky_note():
    """Прилёт от арты: промпт подсказывает LLM съехидничать."""
    arta = build_prompt(game("damage_received", amount=500, source="G.W. Tiger", from_arta=True), [])
    plain = build_prompt(game("damage_received", amount=500, source="Rhm"), [])
    assert "АРТЫ" in arta
    assert "ехидн" in arta.lower()
    assert "АРТЫ" not in plain


def test_arta_fallback_is_dedicated():
    lines = {
        fallback_line(game("damage_received", amount=500, from_arta=True)) for _ in range(30)
    }
    assert all("арт" in line.lower() or "неба" in line.lower() or "кликер" in line.lower() or "фугас" in line.lower() for line in lines)


def test_fallback_exists_for_fixed_chat_effects():
    for t in ("roast", "hype", "stats"):
        line = fallback_line(Stimulus(kind="chat_order", type=t))
        assert isinstance(line, str) and line
