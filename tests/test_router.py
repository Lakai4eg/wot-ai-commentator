import pytest

from wot_ai_commentator.chat.router import ChatRouter
from wot_ai_commentator.chat.twitch import parse_privmsg
from wot_ai_commentator.config import Settings
from wot_ai_commentator.db import WhitelistDB
from wot_ai_commentator.events import Priority


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
    await router.handle("stranger", "!dir привет")
    assert director.submitted == []


@pytest.mark.asyncio
async def test_dir_order_submitted(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    await router.handle("Viewer", "!dir похвали стримера")
    assert len(director.submitted) == 1
    s = director.submitted[0]
    assert s.kind == "chat_order" and s.type == "dir"
    assert s.priority == Priority.HIGH  # заказ не стоит позади игровых событий
    assert s.payload["text"] == "похвали стримера"
    assert s.payload["username"] == "viewer"


@pytest.mark.asyncio
async def test_removed_commands_ignored(env):
    db, director, router = env
    db.add_user("boss", role="admin")
    await router.handle("boss", "!mute 5m")
    await router.handle("boss", "!roast")
    await router.handle("boss", "!stats")
    assert director.submitted == []


@pytest.mark.asyncio
async def test_user_cooldown(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    await router.handle("viewer", "!dir раз")
    await router.handle("viewer", "!dir два")
    assert len(director.submitted) == 1


@pytest.mark.asyncio
async def test_commands_disabled(env):
    db, director, router = env
    db.add_user("viewer", role="director")
    router.settings.chat_commands_enabled = False
    await router.handle("viewer", "!dir привет")
    assert director.submitted == []


@pytest.mark.asyncio
async def test_open_mode_allows_stranger(env):
    _, director, router = env
    router.settings.commands_open_to_all = True
    await router.handle("randomviewer", "!dir привет")
    assert len(director.submitted) == 1


@pytest.mark.asyncio
async def test_banned_blocked_even_in_open_mode(env):
    db, director, router = env
    db.add_user("troll", role="banned")
    router.settings.commands_open_to_all = True  # открыто всем...
    await router.handle("Troll", "!dir спам")
    assert director.submitted == []  # ...но забаненный всё равно заблокирован


@pytest.mark.asyncio
async def test_banned_blocked_in_whitelist_mode(env):
    db, director, router = env
    db.add_user("troll", role="banned")
    await router.handle("troll", "!dir спам")
    assert director.submitted == []
