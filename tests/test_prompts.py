import random

from wot_ai_commentator.commentary.prompts import build_prompt
from wot_ai_commentator.config import Settings
from wot_ai_commentator.events import Priority, Stimulus
from wot_ai_commentator.games.wot.module import build_module as build_wot

MODULE = build_wot(Settings(), submit=lambda s: None)


def game(type_, **payload):
    return Stimulus(kind="game_event", type=type_, game="wot",
                    priority=Priority.NORMAL, payload=payload)


def order(text):
    return Stimulus(
        kind="chat_order", type="dir", priority=Priority.NORMAL,
        payload={"text": text, "username": "viewer1"},
    )


def test_prompt_contains_core_flavor_event_and_facts():
    p = build_prompt(MODULE, game("ammo_rack"), ["это уже 2-я боеукладка"])
    assert "режиссёр" in p.lower()          # ядро персоны
    assert "Мир танков" in p                 # колорит модуля
    assert "боеукладк" in p.lower()          # описание события
    assert "2-я боеукладка" in p             # факты памяти


def test_chat_order_wrapped_and_isolated():
    p = build_prompt(MODULE, order("похвали стримера"), [])
    assert "<заказ>похвали стримера</заказ>" in p
    assert "не инструкции" in p.lower() or "не команды" in p.lower()


def test_chat_order_truncated():
    p = build_prompt(MODULE, order("а" * 500), [])
    start = p.index("<заказ>") + len("<заказ>")
    assert p.index("</заказ>") - start <= 200


def test_arta_hit_gets_snarky_note():
    arta = build_prompt(MODULE, game("damage_received", amount=500, source="G.W.", from_arta=True), [])
    plain = build_prompt(MODULE, game("damage_received", amount=500, source="Rhm"), [])
    assert "АРТЫ" in arta and "АРТЫ" not in plain


def test_session_block_present_when_given():
    p = build_prompt(MODULE, game("frag"), [], ["боёв за сессию: 3"])
    assert "Итоги сессии" in p and "боёв за сессию: 3" in p


def test_address_style_rotates_across_replicas():
    # Стиль обращения подсказывается случайно на каждую реплику: за серию
    # вызовов «маэстро» не должен быть единственным вариантом.
    random.seed(0)
    hints = {
        build_prompt(MODULE, game("frag"), []).rsplit("Обращение к стримеру на этот раз:", 1)[1]
        for _ in range(30)
    }
    assert len(hints) > 1  # обращение реально меняется от реплики к реплике
