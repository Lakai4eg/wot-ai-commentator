"""Тесты клиента WotStat: кормим handle_message фейковым JSON, без реального WS.

Реконнект-луп (run) не поднимаем — по спеке §10 подключение вынесено, а
логика разбора и состояния проверяется через public handle_message.
"""

import json

import pytest

from wot_ai_commentator.wotstat.client import DataProviderClient


def _init(states):
    return json.dumps({"type": "init", "states": states})


def _state(path, value):
    return json.dumps({"type": "state", "path": path, "value": value})


def _trigger(path, value=None):
    return json.dumps({"type": "trigger", "path": path, "value": value})


@pytest.fixture
def client():
    return DataProviderClient()


async def test_init_populates_state_and_get(client):
    await client.handle_message(
        _init([
            {"path": "player.name", "value": "Renou"},
            {"path": "battle.isInBattle", "value": False},
        ])
    )
    assert client.get("player.name") == "Renou"
    assert client.get("battle.isInBattle") is False
    # статус "connected" наступает именно после init
    assert client.status == "connected"


async def test_get_default_for_unknown_path(client):
    assert client.get("nope") is None
    assert client.get("nope", "d") == "d"


async def test_init_notifies_subscriber_with_none_old(client):
    seen = []
    client.subscribe("player.name", lambda new, old: seen.append((new, old)))
    await client.handle_message(_init([{"path": "player.name", "value": "Renou"}]))
    assert seen == [("Renou", None)]


async def test_state_change_notifies_new_old(client):
    seen = []
    client.subscribe("player.name", lambda new, old: seen.append((new, old)))
    await client.handle_message(_init([{"path": "player.name", "value": "Renou"}]))
    await client.handle_message(_state("player.name", "Lakai"))
    assert client.get("player.name") == "Lakai"
    assert seen == [("Renou", None), ("Lakai", "Renou")]


async def test_state_unchanged_does_not_notify(client):
    seen = []
    await client.handle_message(_init([{"path": "battle.health", "value": 100}]))
    client.subscribe("battle.health", lambda new, old: seen.append((new, old)))
    await client.handle_message(_state("battle.health", 100))  # то же значение
    assert seen == []
    await client.handle_message(_state("battle.health", 90))
    assert seen == [(90, 100)]


async def test_state_new_path_old_is_none(client):
    seen = []
    client.subscribe("battle.isAlive", lambda new, old: seen.append((new, old)))
    await client.handle_message(_state("battle.isAlive", True))
    assert seen == [(True, None)]


async def test_trigger_dispatches_value(client):
    got = []
    client.on_trigger("battle.onDamage", lambda v: got.append(v))
    payload = {"damage": 364, "reason": "shot"}
    await client.handle_message(_trigger("battle.onDamage", payload))
    assert got == [payload]


async def test_trigger_null_value(client):
    got = []
    client.on_trigger("battle.onPlayerFeedback", lambda v: got.append(v))
    # Trigger.trigger() без аргумента → value: null
    await client.handle_message(json.dumps({"type": "trigger", "path": "battle.onPlayerFeedback"}))
    assert got == [None]


async def test_reconnect_init_overwrites_and_notifies_only_changed(client):
    seen_name = []
    seen_health = []
    client.subscribe("player.name", lambda new, old: seen_name.append((new, old)))
    client.subscribe("battle.health", lambda new, old: seen_health.append((new, old)))
    await client.handle_message(
        _init([
            {"path": "player.name", "value": "Renou"},
            {"path": "battle.health", "value": 100},
        ])
    )
    # новый init (реконнект): name тот же, health изменился
    await client.handle_message(
        _init([
            {"path": "player.name", "value": "Renou"},
            {"path": "battle.health", "value": 50},
        ])
    )
    assert seen_name == [("Renou", None)]  # не изменился — второй раз не звали
    assert seen_health == [(100, None), (50, 100)]
    assert client.get("battle.health") == 50


async def test_broken_json_does_not_crash(client):
    await client.handle_message("это не json {{{")
    await client.handle_message("[1, 2, 3]")  # валидный json, но не объект
    await client.handle_message(json.dumps({"type": "unknown", "x": 1}))
    # клиент жив и обрабатывает нормальные сообщения дальше
    await client.handle_message(_state("player.name", "ok"))
    assert client.get("player.name") == "ok"
    assert client.last_event_at is not None


async def test_callback_error_is_caught(client):
    calls = []

    def boom(new, old):
        raise RuntimeError("bad callback")

    client.subscribe("player.name", boom)
    client.subscribe("player.name", lambda new, old: calls.append(new))
    # первый подписчик падает — второй всё равно вызывается, клиент не роняется
    await client.handle_message(_state("player.name", "Renou"))
    assert calls == ["Renou"]


async def test_trigger_callback_error_is_caught(client):
    calls = []
    client.on_trigger("battle.onDamage", lambda v: (_ for _ in ()).throw(RuntimeError()))
    client.on_trigger("battle.onDamage", lambda v: calls.append(v))
    await client.handle_message(_trigger("battle.onDamage", {"damage": 1}))
    assert calls == [{"damage": 1}]


async def test_last_event_at_updates(client):
    assert client.last_event_at is None
    await client.handle_message(_state("player.name", "Renou"))
    assert isinstance(client.last_event_at, float)
