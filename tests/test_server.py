
import asyncio
import time

import httpx
import pytest

from wot_ai_commentator.config import Settings
from wot_ai_commentator.db import WhitelistDB
from wot_ai_commentator.director import Director
from wot_ai_commentator.events import Priority, Stimulus
from wot_ai_commentator.games.base import ActiveGameTracker
from wot_ai_commentator.games.wot.module import build_module as build_wot
from wot_ai_commentator.server import AppContext, create_app


class FakeBackend:
    last_error = None

    async def generate(self, prompt):
        return "тестовая реплика"


@pytest.fixture
def ctx(tmp_path):
    settings = Settings()
    db = WhitelistDB(tmp_path / "wl.db")
    tracker = ActiveGameTracker()
    c = AppContext(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        db=db,
        director=None,
        tracker=tracker,
    )
    c.director = Director(settings, FakeBackend(), c.publish, tracker)
    c.director.register(build_wot(settings, submit=lambda s: None))
    yield c
    db.close()


@pytest.fixture
def client(ctx):
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_users_crud(client, ctx):
    r = await client.post("/api/users", json={"username": "MakeMeFly", "role": "admin"})
    assert r.status_code == 201
    r = await client.get("/api/users")
    users = r.json()
    assert users[0]["username"] == "makemefly"
    assert users[0]["role"] == "admin"

    r = await client.delete("/api/users/twitch/makemefly")
    assert r.status_code == 200
    assert (await client.get("/api/users")).json() == []

    r = await client.delete("/api/users/twitch/nope")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bad_role_rejected(client):
    r = await client.post("/api/users", json={"username": "x", "role": "root"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_banned_role_accepted(client, ctx):
    r = await client.post("/api/users", json={"username": "troll", "role": "banned"})
    assert r.status_code == 201
    assert ctx.db.get_role("troll") == "banned"


@pytest.mark.asyncio
async def test_open_commands_setting_persists(client, ctx):
    r = await client.put("/api/settings", json={"commands_open_to_all": True})
    assert r.status_code == 200
    assert ctx.settings.commands_open_to_all is True
    assert r.json()["commands_open_to_all"] is True


@pytest.mark.asyncio
async def test_settings_put_persists(client, ctx):
    r = await client.put(
        "/api/settings",
        json={"twitch_channel": "chan", "global_cooldown_s": 7.0, "tts_max_age_s": 12.0},
    )
    assert r.status_code == 200
    assert ctx.settings.twitch_channel == "chan"
    assert ctx.settings.global_cooldown_s == 7.0
    assert ctx.settings.tts_max_age_s == 12.0
    assert ctx.settings_path.exists()

    r = await client.put("/api/settings", json={"llm_provider": "wat"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_key_masked(client, ctx):
    ctx.settings.gemini_api_key = "secret-key"
    r = await client.get("/api/settings")
    assert "secret" not in r.text


@pytest.mark.asyncio
async def test_llm_test_endpoint(client, ctx):
    class ProbeBackend:
        last_error = None

        async def generate(self, prompt):
            return "на связи"

    ctx.backend = ProbeBackend()
    body = (await client.post("/api/llm/test")).json()
    assert body["ok"] is True
    assert body["reply"] == "на связи"

    class DeadBackend:
        last_error = "HTTP 401"

        async def generate(self, prompt):
            return None

    ctx.backend = DeadBackend()
    body = (await client.post("/api/llm/test")).json()
    assert body["ok"] is False
    assert body["error"] == "HTTP 401"


@pytest.mark.asyncio
async def test_status_ok(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "director" in body


@pytest.mark.asyncio
async def test_status_exposes_wotstat(client, ctx):
    # main.py кладёт блок wotstat в statuses; /api/status отдаёт его как есть.
    ctx.statuses["wotstat"] = {"status": "waiting", "game_state": None}
    body = (await client.get("/api/status")).json()
    assert body["wotstat"]["status"] == "waiting"
    assert "detector" not in body
    assert "detector_diag" not in body




@pytest.mark.asyncio
async def test_status_exposes_active_game_and_memory(client, ctx):
    body = (await client.get("/api/status")).json()
    assert body["active_game"] == "wot"
    assert isinstance(body["memory"], list)


def test_voice_fresh_gate(ctx):
    ctx.settings.tts_max_age_s = 20.0
    fresh = Stimulus(kind="game_event", type="frag")
    stale = Stimulus(kind="game_event", type="frag", created_at=time.time() - 100)
    assert ctx._voice_fresh(fresh) is True
    assert ctx._voice_fresh(stale) is False


@pytest.mark.asyncio
async def test_stale_event_skips_voice(ctx):
    """Свежее событие озвучивается, устаревшее — только текст, без TTS."""
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.settings.voice_enabled = True
    ctx.settings.tts_max_age_s = 20.0
    ctx.tts = FakeTTS()

    voiced: list[tuple[str, str]] = []

    async def record(replica_id, text, voice):
        voiced.append((text, voice))

    ctx._send_audio = record  # перехватываем факт озвучки

    ctx.settings.voice_by_priority = {"high": "aidar"}
    await ctx.publish("свежая", Stimulus(kind="game_event", type="frag", priority=Priority.HIGH))
    await asyncio.sleep(0)  # даём фоновой задаче озвучки стартовать
    assert voiced == [("свежая", "aidar")]

    voiced.clear()
    await ctx.publish(
        "запоздавшая",
        Stimulus(kind="game_event", type="frag", created_at=time.time() - 100),
    )
    await asyncio.sleep(0)
    assert voiced == []  # устаревшую реплику не озвучили


@pytest.mark.asyncio
async def test_audio_roundtrip(client, ctx):
    audio_id = ctx.audio.put(b"RIFFfake")
    r = await client.get(f"/api/audio/{audio_id}")
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert (await client.get("/api/audio/999")).status_code == 404


@pytest.mark.asyncio
async def test_voice_settings_persist(client, ctx):
    r = await client.put(
        "/api/settings",
        json={
            "default_voice": "xenia",
            "voice_by_priority": {"high": "aidar"},
            "voice_overrides": {"death": "eugene"},
        },
    )
    assert r.status_code == 200
    assert ctx.settings.default_voice == "xenia"
    assert ctx.settings.voice_by_priority == {"high": "aidar"}
    assert ctx.settings.voice_overrides == {"death": "eugene"}
    body = r.json()
    assert body["default_voice"] == "xenia"


@pytest.mark.asyncio
async def test_voices_list(client):
    body = (await client.get("/api/voices")).json()
    assert "baya" in body["voices"]
    assert "aidar" in body["voices"]


@pytest.mark.asyncio
async def test_preview_503_without_tts(client, ctx):
    ctx.tts = None
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_preview_returns_wav(client, ctx):
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.tts = FakeTTS()
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert r.headers["content-type"] == "audio/wav"
