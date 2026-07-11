
import asyncio
import time

import httpx
import pytest

from stream_director.broadcast import OverlayBroadcaster
from stream_director.commentary.gemini import GeminiBackend
from stream_director.commentary.openai_compat import OpenAICompatBackend
from stream_director.commentary.switch import SwitchBackend
from stream_director.config import Settings
from stream_director.db import ChatUserDB
from stream_director.director import Director
from stream_director.stimulus import Priority, Stimulus
from stream_director.games.base import ActiveGameTracker
from stream_director.games.wot.module import build_module as build_wot
from stream_director.server import AppContext, create_app


class FakeBackend:
    last_error = None

    async def generate(self, prompt):
        return "тестовая реплика"


@pytest.fixture
def ctx(tmp_path):
    settings = Settings()
    db = ChatUserDB(tmp_path / "wl.db")
    tracker = ActiveGameTracker()
    broadcaster = OverlayBroadcaster(settings)
    director = Director(settings, FakeBackend(), broadcaster.publish, tracker)
    director.register(build_wot(settings, submit=lambda s: None))
    c = AppContext(
        settings=settings,
        settings_path=tmp_path / "settings.json",
        db=db,
        director=director,
        broadcaster=broadcaster,
        tracker=tracker,
    )
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
async def test_masked_key_put_does_not_overwrite(client, ctx):
    """PUT маски из GET («••••••••») не затирает сохранённый ключ."""
    ctx.settings.gemini_api_key = "secret-key"
    r = await client.put("/api/settings", json={"gemini_api_key": "•" * 8})
    assert r.status_code == 200
    assert ctx.settings.gemini_api_key == "secret-key"
    # Пустая строка — легальный способ стереть ключ.
    r = await client.put("/api/settings", json={"gemini_api_key": ""})
    assert r.status_code == 200
    assert ctx.settings.gemini_api_key == ""


@pytest.mark.asyncio
async def test_negative_timings_rejected(client, ctx):
    r = await client.put("/api/settings", json={"global_cooldown_s": -1.0})
    assert r.status_code == 400
    assert ctx.settings.global_cooldown_s == Settings().global_cooldown_s


@pytest.mark.asyncio
async def test_put_settings_applies_to_backend(client, ctx):
    """PUT донастраивает провайдеров через SwitchBackend.apply, не через внутренности."""
    backend = SwitchBackend(ctx.settings, GeminiBackend(""), OpenAICompatBackend())
    backend.gemini.last_error = "HTTP 401"
    ctx.backend = backend
    r = await client.put("/api/settings", json={
        "gemini_api_key": "k1",
        "openai_base_url": "https://api.example/v1",
        "openai_model": "m1",
    })
    assert r.status_code == 200
    assert backend.gemini.api_key == "k1"
    assert backend.gemini.last_error is None  # новый ключ — старая ошибка неактуальна
    assert backend.openai.base_url == "https://api.example/v1"
    assert backend.openai.model == "m1"


@pytest.mark.asyncio
async def test_status_refreshed_on_demand(client, ctx):
    """GET /api/status зовёт refresh_status — фоновый полл больше не нужен."""
    calls = []

    def refresh():
        calls.append(1)
        ctx.statuses["chat"] = "connected"

    ctx.refresh_status = refresh
    body = (await client.get("/api/status")).json()
    assert calls == [1]
    assert body["chat"] == "connected"


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
    assert ctx.broadcaster._voice_fresh(fresh) is True
    assert ctx.broadcaster._voice_fresh(stale) is False


@pytest.mark.asyncio
async def test_stale_event_skips_voice(ctx):
    """Свежее событие озвучивается, устаревшее — только текст, без TTS."""
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.settings.voice_enabled = True
    ctx.settings.tts_max_age_s = 20.0
    ctx.broadcaster.tts = FakeTTS()

    voiced: list[tuple[str, str]] = []

    async def record(replica_id, text, voice):
        voiced.append((text, voice))

    ctx.broadcaster._send_audio = record  # перехватываем факт озвучки

    ctx.settings.voice_by_priority = {"high": "aidar"}
    await ctx.broadcaster.publish(
        "свежая", Stimulus(kind="game_event", type="frag", priority=Priority.HIGH)
    )
    await asyncio.sleep(0)  # даём фоновой задаче озвучки стартовать
    assert voiced == [("свежая", "aidar")]

    voiced.clear()
    await ctx.broadcaster.publish(
        "запоздавшая",
        Stimulus(kind="game_event", type="frag", created_at=time.time() - 100),
    )
    await asyncio.sleep(0)
    assert voiced == []  # устаревшую реплику не озвучили


@pytest.mark.asyncio
async def test_audio_roundtrip(client, ctx):
    audio_id = ctx.broadcaster.audio.put(b"RIFFfake")
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
    ctx.broadcaster.tts = None
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_preview_returns_wav(client, ctx):
    class FakeTTS:
        available = True

        def synth(self, text, voice=None):
            return b"RIFFfake"

    ctx.broadcaster.tts = FakeTTS()
    r = await client.post("/api/tts/preview", json={"voice": "aidar"})
    assert r.status_code == 200
    assert r.content == b"RIFFfake"
    assert r.headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_status_reports_app_version(client):
    from stream_director import __version__

    r = await client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["app_version"] == __version__
