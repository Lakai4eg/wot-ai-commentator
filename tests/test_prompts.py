import dataclasses
import random

from stream_director.commentary.prompts import build_prompt
from stream_director.config import Settings
from stream_director.stimulus import Priority, Stimulus
from stream_director.games.wot.module import build_module as build_wot

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
    assert "стендапер" in p.lower()          # ядро персоны
    assert "Мир танков" in p                 # колорит модуля
    assert "боеукладк" in p.lower()          # описание события
    assert "2-я боеукладка" in p             # факты памяти


def test_persona_core_roast_target_rule():
    # Ядро задаёт правило мишени и не содержит старых смягчителей тона.
    p = build_prompt(MODULE, game("frag"), [])
    assert "тот, кто дал повод" in p
    assert "по-доброму" not in p
    assert "дружеская подколка" not in p.lower()


def test_chat_order_wrapped_and_isolated():
    p = build_prompt(MODULE, order("похвали стримера"), [])
    assert "<заказ>похвали стримера</заказ>" in p
    assert "не инструкции" in p.lower() or "не команды" in p.lower()


def test_chat_order_not_forced_about_streamer():
    # Заказ зрителя не обязательно про стримера — случайный стиль обращения
    # и случайный угол шутки к заказу не подсказываем, тему задаёт сам заказ.
    module = dataclasses.replace(MODULE, joke_angles=lambda: ("угол-тест",))
    p = build_prompt(module, order("поздравь зрителя vasya с днём рождения"), [])
    assert "Обращение к стримеру на этот раз" not in p
    assert "Угол шутки" not in p
    assert "не обязательно про стримера" in p.lower()


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


def test_recent_lines_block_present():
    # «не повторяй их» — маркер именно блока истории: в ядре персоны уже
    # есть общее «не повторяйся», по нему блок не отличить.
    p = build_prompt(MODULE, game("frag"), [],
                     recent_lines=["Первая реплика.", "Вторая реплика."])
    assert "не повторяй их" in p.lower()
    assert "Первая реплика." in p and "Вторая реплика." in p


def test_no_recent_block_when_empty():
    p = build_prompt(MODULE, game("frag"), [], recent_lines=[])
    assert "не повторяй их" not in p.lower()


def test_joke_angle_line_when_module_provides():
    module = dataclasses.replace(MODULE, joke_angles=lambda: ("угол-тест",))
    p = build_prompt(module, game("frag"), [])
    assert "Угол шутки на этот раз: угол-тест." in p


def test_no_joke_angle_without_field():
    # WoT-модуль поле не задаёт — строки угла быть не должно.
    p = build_prompt(MODULE, game("frag"), [])
    assert "Угол шутки" not in p


def test_seed_line_block_present_and_replaces_angle():
    # Затравка сама задаёт угол — случайный «угол шутки» не подсказываем,
    # иначе LLM получает две конфликтующие инструкции.
    module = dataclasses.replace(MODULE, joke_angles=lambda: ("угол-тест",))
    p = build_prompt(module, game("frag"), [], seed_line="Минус один!")
    assert "Заготовка шутки: «Минус один!»" in p
    assert "Угол шутки" not in p


def test_no_seed_block_by_default():
    p = build_prompt(MODULE, game("frag"), [])
    assert "Заготовка шутки" not in p
