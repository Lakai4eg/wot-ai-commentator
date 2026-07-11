import time

import pytest

from stream_director.config import Settings
from stream_director.director import Director
from stream_director.stimulus import Priority, Stimulus
from stream_director.games.base import ActiveGameTracker
from stream_director.games.wot.module import build_module as build_wot


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

    tracker = ActiveGameTracker()
    d = Director(settings, backend or FakeBackend(), publish, tracker)
    d.register(build_wot(settings, submit=lambda s: None))
    return d, published


def game(type_, priority=Priority.NORMAL, **payload):
    return Stimulus(kind="game_event", type=type_, game="wot",
                    priority=priority, payload=payload)


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
    d.submit(Stimulus(kind="chat_order", type="dir",
                      payload={"text": "скажи привет", "username": "u"}))
    await drain(d)
    assert len(published) == 1


@pytest.mark.asyncio
async def test_replica_times_pruned_to_last_minute():
    """Метки реплик старше минуты вычищаются — список не растёт весь стрим."""
    d, _ = make_director()
    d._replica_times = [time.time() - 120.0] * 50  # накопленный «хвост»
    d.submit(game("frag"))
    await drain(d)
    assert len(d._replica_times) == 1
    assert d.stats()["replicas_last_minute"] == 1


@pytest.mark.asyncio
async def test_expired_stimulus_dropped():
    d, published = make_director()
    stale = game("frag")
    stale.created_at = time.time() - 100
    d.submit(stale)
    await drain(d)
    assert published == []


@pytest.mark.asyncio
async def test_dir_order_bypasses_global_cooldown():
    """Заказ из чата отрабатывает всегда — кулдаун между репликами его не глушит."""
    d, published = make_director()
    d.settings.global_cooldown_s = 60.0
    d.submit(game("frag"))
    await drain(d)  # обычная реплика забрала кулдаун
    d.submit(Stimulus(kind="chat_order", type="dir",
                      payload={"text": "скажи привет", "username": "u"}))
    await drain(d)
    assert [s.kind for _, s in published] == ["game_event", "chat_order"]


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
    assert d.games["wot"].memory.battle.map == "Химмельсдорф"


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
async def test_chat_order_routes_to_active_game():
    backend = FakeBackend()
    d, _ = make_director(backend=backend)
    d.SESSION_TEASE_PROB = 0.0
    d.tracker.mark_live("wot")
    d.submit(Stimulus(kind="chat_order", type="dir",
                      payload={"text": "подколи стримера", "username": "u"}))
    await drain(d)
    assert "Мир танков" in backend.prompts[-1]


@pytest.mark.asyncio
async def test_unknown_game_falls_back_to_active():
    d, published = make_director()
    d.submit(Stimulus(kind="game_event", type="frag", game="quake"))
    await drain(d)
    assert len(published) == 1  # не упали, обработали активным модулем


from stream_director.games.lol.module import build_module as build_lol


@pytest.mark.asyncio
async def test_lol_stimulus_routes_to_lol_module():
    backend = FakeBackend()
    d, published = make_director(backend=backend)
    d.SESSION_TEASE_PROB = 0.0
    d.register(build_lol(Settings(), submit=lambda s: None))
    d.submit(Stimulus(kind="game_event", type="frag", game="lol",
                      priority=Priority.HIGH, payload={"target": "Darius"}))
    await drain(d)
    assert len(published) == 1
    assert "League of Legends" in backend.prompts[-1]
    assert d.games["lol"].memory.battle.kills == 1
    assert d.games["wot"].memory.battle.frags == 0  # память WoT не тронута


@pytest.mark.asyncio
async def test_lol_multikill_bypasses_cooldown():
    d, published = make_director()
    d.register(build_lol(Settings(), submit=lambda s: None))
    d.settings.global_cooldown_s = 60.0
    d.submit(Stimulus(kind="game_event", type="frag", game="lol",
                      priority=Priority.HIGH, payload={"target": "Darius"}))
    await drain(d)
    d.submit(Stimulus(kind="game_event", type="multikill", game="lol",
                      priority=Priority.CRITICAL,
                      payload={"count": 5, "label": "пентакилл"}))
    await drain(d)
    assert [s.type for _, s in published] == ["frag", "multikill"]


@pytest.mark.asyncio
async def test_recent_replicas_fed_back_into_prompt():
    """Прошлые реплики попадают в следующий промпт с запретом повтора."""
    backend = FakeBackend(reply="Уникальная шутка про фраг")
    d, published = make_director(backend)
    d.submit(game("frag"))
    await drain(d)
    d.submit(game("frag"))
    await drain(d)
    assert len(published) == 2
    assert "Уникальная шутка про фраг" in backend.prompts[1]
    # «не повторяй их» — маркер блока истории (в ядре персоны уже есть
    # общее «не повторяйся», по нему блок не отличить).
    assert "не повторяй их" in backend.prompts[1].lower()
    # В первый промпт истории ещё нет.
    assert "не повторяй их" not in backend.prompts[0].lower()
