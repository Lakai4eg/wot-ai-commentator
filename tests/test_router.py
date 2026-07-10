import pytest

from wot_ai_commentator.chat.router import ChatRouter
from wot_ai_commentator.chat.twitch import parse_privmsg
from wot_ai_commentator.config import Settings
from wot_ai_commentator.db import WhitelistDB


class FakeDirector:
    def __init__(self):
        self.submitted = []

    def submit(self, stimulus):
        self.submitted.append(stimulus)


@pytest.fixture
def env(tmp_path):
    db = WhitelistDB(tmp_path / "wl.db")
    director = FakeDirector()
    settings = Settings(user_cooldown_s=60.0)
    router = ChatRouter(db, director, settings)
    yield db, director, router
    db.close()


def test_parse_privmsg():
    line = ":makemefly!makemefly@makemefly.tmi.twitch.tv PRIVMSG #somechan :!dir привет мир"
    assert parse_privmsg(line) == ("makemefly", "!dir привет мир")
    assert parse_privmsg("PING :tmi.twitch.tv") is None


@pytest.mark.asyncio
async def test_stranger_ignored(env):
    _, director, router = env
    await router.handle("stranger", "!roast")
    assert director.submitted == []


@pytest.mark.asyncio
async def test_director_role_cannot_mute(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    await router.handle("viewer", "!mute 5m")
    assert director.submitted == []


@pytest.mark.asyncio
async def test_admin_can_mute(env):
    db, director, router = env
    db.add_user("boss", role="admin")
    await router.handle("boss", "!mute 5m")
    kinds = [(s.kind, s.type) for s in director.submitted]
    assert ("control", "mute") in kinds
    mute = next(s for s in director.submitted if s.type == "mute")
    assert mute.payload["seconds"] == 300


@pytest.mark.asyncio
async def test_dir_order_submitted(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    await router.handle("Viewer", "!dir похвали стримера")
    assert len(director.submitted) == 1
    s = director.submitted[0]
    assert s.kind == "chat_order" and s.type == "dir"
    assert s.payload["text"] == "похвали стримера"
    assert s.payload["username"] == "viewer"


@pytest.mark.asyncio
async def test_user_cooldown(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    await router.handle("viewer", "!roast")
    await router.handle("viewer", "!hype")
    assert len(director.submitted) == 1


@pytest.mark.asyncio
async def test_commands_disabled(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    router.settings.chat_commands_enabled = False
    await router.handle("viewer", "!roast")
    assert director.submitted == []
