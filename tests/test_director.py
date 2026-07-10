import time

import pytest

from wot_ai_commentator.config import Settings
from wot_ai_commentator.director import Director
from wot_ai_commentator.events import Priority, Stimulus
from wot_ai_commentator.session_memory import SessionMemory


class FakeBackend:
    def __init__(self, reply="реплика"):
        self.reply = reply
        self.prompts = []
        self.last_error = None

    async def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def make_director(backend=None, **overrides):
    overrides.setdefault("debounce_s", 0.0)  # дебаунс выключен, если не задан
    settings = Settings(global_cooldown_s=0.0, **overrides)
    published = []

    async def publish(text, stimulus):
        published.append((text, stimulus))

    d = Director(settings, SessionMemory(), backend or FakeBackend(), publish)
    return d, published


def game(type_, priority=Priority.NORMAL, **payload):
    return Stimulus(kind="game_event", type=type_, priority=priority, payload=payload)


async def drain(director, n_cycles=10):
    for _ in range(n_cycles):
        await director.process_once()


@pytest.mark.asyncio
async def test_generates_and_publishes():
    d, published = make_director()
    d.submit(game("frag"))
    await drain(d)
    assert len(published) == 1
    assert published[0][0] == "реплика"


@pytest.mark.asyncio
async def test_priority_order():
    d, published = make_director()
    d.submit(game("frag", Priority.NORMAL))
    d.submit(game("ammo_rack", Priority.CRITICAL))
    await drain(d)
    assert [s.type for _, s in published] == ["ammo_rack", "frag"]


@pytest.mark.asyncio
async def test_global_cooldown_drops_second():
    d, published = make_director()
    d.settings.global_cooldown_s = 60.0
    d.submit(game("frag"))
    d.submit(game("frag"))
    await drain(d)
    assert len(published) == 1


@pytest.mark.asyncio
async def test_death_bypasses_cooldown():
    """Смерть выходит всегда, даже когда кулдаун только что сработал."""
    d, published = make_director()
    d.settings.global_cooldown_s = 60.0
    # Обычная реплика забирает кулдаун — следующий frag был бы отброшен...
    d.submit(game("frag"))
    await drain(d)
    d.submit(game("frag"))
    d.submit(game("death", Priority.HIGH, killer="Убийца"))
    await drain(d)
    assert [s.type for _, s in published] == ["frag", "death"]


@pytest.mark.asyncio
async def test_debounce_holds_low_event_during_burst():
    """Мелкое событие в разгар бури придерживается, реплика не выходит."""
    d, published = make_director(debounce_s=1.2, debounce_max_s=5.0)
    d._last_game_event_at = time.time()  # события только что сыпались
    d.submit(game("spotted", Priority.LOW))
    await drain(d)
    assert published == []  # ещё бурлит — молчим


@pytest.mark.asyncio
async def test_debounce_releases_after_settle():
    """Когда буря улеглась (пауза ≥ debounce_s), придержанное событие выходит."""
    d, published = make_director(debounce_s=1.2, debounce_max_s=5.0)
    d.submit(game("spotted", Priority.LOW))
    d._last_game_event_at = time.time() - 2.0  # 2 с тишины — буря улеглась
    await drain(d)
    assert [s.type for _, s in published] == ["spotted"]


@pytest.mark.asyncio
async def test_debounce_cap_flushes_sustained_burst():
    """В затяжном замесе кэп debounce_max_s не даёт замолчать навсегда."""
    d, published = make_director(debounce_s=1.2, debounce_max_s=5.0)
    d._last_game_event_at = time.time()  # буря всё ещё идёт
    stale = game("spotted", Priority.LOW)
    stale.created_at = time.time() - 6.0  # событие ждёт дольше кэпа
    d.submit(stale)
    d._last_game_event_at = time.time()  # submit сдвинул метку — вернём «бурю»
    await drain(d)
    assert [s.type for _, s in published] == ["spotted"]


@pytest.mark.asyncio
async def test_debounce_bypassed_for_big_moment():
    """Крупные события (HIGH/CRITICAL) дебаунс не задерживает."""
    d, published = make_director(debounce_s=1.2, debounce_max_s=5.0)
    d._last_game_event_at = time.time()  # буря идёт
    d.submit(game("frag", Priority.HIGH))
    await drain(d)
    assert [s.type for _, s in published] == ["frag"]


@pytest.mark.asyncio
async def test_debounce_bypassed_for_chat_order():
    """Заказ из чата отвечается сразу, дебаунс его не держит."""
    d, published = make_director(debounce_s=1.2, debounce_max_s=5.0)
    d._last_game_event_at = time.time()  # буря идёт
    d.submit(Stimulus(kind="chat_order", type="roast", payload={"username": "u"}))
    await drain(d)
    assert len(published) == 1


@pytest.mark.asyncio
async def test_expired_stimulus_dropped():
    d, published = make_director()
    stale = game("frag")
    stale.created_at = time.time() - 100
    d.submit(stale)
    await drain(d)
    assert published == []


@pytest.mark.asyncio
async def test_mute_drops_replicas():
    d, published = make_director()
    d.submit(Stimulus(kind="control", type="mute", payload={"seconds": 60}))
    await drain(d)
    d.submit(game("ammo_rack", Priority.CRITICAL))
    await drain(d)
    assert published == []
    assert d.muted_until > time.time()


@pytest.mark.asyncio
async def test_backend_none_falls_back_for_game_event():
    class NoneBackend(FakeBackend):
        async def generate(self, prompt):
            return None

    d, published = make_director(backend=NoneBackend())
    d.submit(game("ammo_rack"))
    await drain(d)
    assert len(published) == 1
    assert published[0][0]  # шаблонная фраза


@pytest.mark.asyncio
async def test_backend_none_silences_dir_order():
    class NoneBackend(FakeBackend):
        async def generate(self, prompt):
            return None

    d, published = make_director(backend=NoneBackend())
    d.submit(Stimulus(kind="chat_order", type="dir",
                      payload={"text": "привет", "username": "u"}))
    await drain(d)
    assert published == []


@pytest.mark.asyncio
async def test_silent_stimulus_registers_memory_no_reply():
    # Тихое событие (§4.2): память обновляется, реплика не рождается.
    d, published = make_director()
    d.submit(game("battle_start", map="Химмельсдорф", silent=True))
    await drain(d)
    assert published == []
    assert d.memory.battle.map == "Химмельсдорф"


@pytest.mark.asyncio
async def test_battle_context_always_session_rarely():
    """Реплика строится от текущего боя; сессия — только при «подколке»."""
    backend = FakeBackend()
    d, _ = make_director(backend=backend)
    d.SESSION_TEASE_PROB = 0.0  # подколка выключена
    d.submit(game("battle_start", map="Прохоровка", silent=True))
    d.submit(game("battle_result", outcome="win", silent=True))
    d.submit(game("frag", target="Vasya"))
    await drain(d)
    prompt = backend.prompts[-1]
    assert "Текущий бой:" in prompt
    assert "Прохоровка" in prompt
    assert "Итоги сессии" not in prompt

    d.SESSION_TEASE_PROB = 1.0  # подколка гарантирована
    d.submit(game("frag", target="Petya"))
    await drain(d)
    prompt = backend.prompts[-1]
    assert "Итоги сессии" in prompt
    assert "боёв за сессию: 1, побед: 1" in prompt


@pytest.mark.asyncio
async def test_stats_order_always_gets_session():
    backend = FakeBackend()
    d, _ = make_director(backend=backend)
    d.SESSION_TEASE_PROB = 0.0
    d.submit(game("battle_result", outcome="win", silent=True))
    d.submit(Stimulus(kind="chat_order", type="stats", payload={"username": "u"}))
    await drain(d)
    assert "Итоги сессии" in backend.prompts[-1]
