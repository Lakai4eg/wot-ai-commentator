"""Миграция БД со старых имён приложения и супервизор фоновых задач."""

import asyncio
import os
import time

import pytest

from stream_director.main import migrate_legacy_db, supervised


def test_no_files_is_noop(tmp_path):
    target = tmp_path / "chat-users.db"
    migrate_legacy_db(tmp_path, target)
    assert not target.exists()


def test_target_exists_untouched(tmp_path):
    target = tmp_path / "chat-users.db"
    target.write_bytes(b"current")
    (tmp_path / "wot-ai-commentator.db").write_bytes(b"legacy")
    migrate_legacy_db(tmp_path, target)
    assert target.read_bytes() == b"current"
    assert (tmp_path / "wot-ai-commentator.db").exists()


def test_single_legacy_renamed(tmp_path):
    target = tmp_path / "chat-users.db"
    legacy = tmp_path / "stream-director.db"
    legacy.write_bytes(b"legacy")
    migrate_legacy_db(tmp_path, target)
    assert target.read_bytes() == b"legacy"
    assert not legacy.exists()


def test_newest_legacy_wins(tmp_path):
    target = tmp_path / "chat-users.db"
    old = tmp_path / "stream-director.db"
    new = tmp_path / "wot-ai-commentator.db"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    past = time.time() - 3600
    os.utime(old, (past, past))
    migrate_legacy_db(tmp_path, target)
    assert target.read_bytes() == b"new"
    assert old.exists()  # проигравший кандидат не трогаем


@pytest.mark.asyncio
async def test_supervised_restarts_crashed_task():
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    await asyncio.wait_for(supervised("flaky", flaky, retry_s=0.0), timeout=1.0)
    assert len(calls) == 2  # первая попытка упала, вторая завершилась штатно


@pytest.mark.asyncio
async def test_supervised_returns_on_clean_exit():
    calls = []

    async def clean():
        calls.append(1)

    await asyncio.wait_for(supervised("clean", clean), timeout=1.0)
    assert calls == [1]  # штатное завершение не перезапускается
