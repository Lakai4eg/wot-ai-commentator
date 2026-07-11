import asyncio

import pytest

from stream_director.chat.twitch import TwitchChatReader


async def _noop(user: str, text: str) -> None:
    pass


class FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_set_channel_normalizes():
    reader = TwitchChatReader("old", _noop)
    reader.set_channel("  #NewChan ")
    assert reader.channel == "newchan"


def test_set_channel_drops_connection():
    reader = TwitchChatReader("old", _noop)
    writer = FakeWriter()
    reader._writer = writer
    reader.set_channel("newchan")
    assert writer.closed


def test_set_channel_same_channel_keeps_connection():
    reader = TwitchChatReader("Same", _noop)
    writer = FakeWriter()
    reader._writer = writer
    reader.set_channel("#same")
    assert not writer.closed


@pytest.mark.asyncio
async def test_run_picks_up_channel_set_later(monkeypatch):
    """Пустой канал при старте: run() ждёт настройки, а не завершается."""
    reader = TwitchChatReader("", _noop)
    connected_to = []

    async def fake_session():
        connected_to.append(reader.channel)
        reader.stop()

    monkeypatch.setattr(reader, "_session", fake_session)
    task = asyncio.create_task(reader.run())
    await asyncio.sleep(0.05)
    assert not task.done()
    assert connected_to == []

    reader.set_channel("mychan")
    await asyncio.wait_for(task, timeout=2)
    assert connected_to == ["mychan"]


@pytest.mark.asyncio
async def test_channel_change_reconnects_without_backoff(monkeypatch):
    """Смена канала будит реконнект сразу, не дожидаясь паузы backoff."""
    reader = TwitchChatReader("first", _noop)
    sessions = []

    async def fake_session():
        sessions.append(reader.channel)
        if len(sessions) == 1:
            raise ConnectionError("соединение закрыто")
        reader.stop()

    monkeypatch.setattr(reader, "_session", fake_session)
    task = asyncio.create_task(reader.run())
    await asyncio.sleep(0.05)
    assert sessions == ["first"]

    reader.set_channel("second")
    await asyncio.wait_for(task, timeout=1)  # меньше backoff в 2 секунды
    assert sessions == ["first", "second"]
