# tests/test_lol_client.py
from stream_director.games.lol.client import LiveClientPoller


def test_initial_status_waiting():
    c = LiveClientPoller()
    assert c.status == "waiting"
    assert c.last_event_at is None


def test_handle_payload_dispatches_and_stamps_time():
    got = []
    c = LiveClientPoller(on_payload=got.append)
    c.handle_payload({"gameData": {"gameTime": 1.0}})
    assert got == [{"gameData": {"gameTime": 1.0}}]
    assert c.last_event_at is not None


def test_handle_payload_survives_broken_callback():
    def boom(data):
        raise RuntimeError("шоу продолжается")

    c = LiveClientPoller(on_payload=boom)
    c.handle_payload({})  # не должно бросить


def test_mark_live_fires_on_transition_only():
    calls = []
    c = LiveClientPoller(on_live=lambda: calls.append(1))
    c._mark_live()
    c._mark_live()
    assert c.status == "connected"
    assert calls == [1]
    c.status = "waiting"
    c._mark_live()
    assert calls == [1, 1]
