"""Директор: приоритетная очередь стимулов → LLM → публикация реплик."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import random
import time
from typing import Awaitable, Callable

from .commentary.base import CommentaryBackend
from .commentary.prompts import build_prompt
from .commentary.templates import fallback_line
from .config import Settings
from .events import Priority, Stimulus
from .session_memory import SessionMemory

log = logging.getLogger(__name__)

PublishFn = Callable[[str, Stimulus], Awaitable[None]]

# События, ради которых нарушаем кулдаун: реплика выходит всегда (смерть — раз
# за бой, драматичный момент, теряться в тишине кулдауна ей нельзя).
ALWAYS_SPEAK_TYPES = frozenset({"death"})


class Director:
    # Как часто в промпт попадают итоги сессии (редкие подколки поверх
    # контекста текущего боя). Для !stats сессия включается всегда.
    SESSION_TEASE_PROB = 0.2

    def __init__(
        self,
        settings: Settings,
        memory: SessionMemory,
        backend: CommentaryBackend,
        publish: PublishFn,
    ):
        self.settings = settings
        self.memory = memory
        self.backend = backend
        self.publish = publish

        self.muted_until = 0.0

        self._heap: list[tuple[int, int, Stimulus]] = []
        self._counter = itertools.count()
        self._last_replica_at = 0.0
        self._last_game_event_at = 0.0  # для дебаунса: когда сыпались события
        self._replica_times: list[float] = []
        self._wakeup = asyncio.Event()
        self._running = False

    # --- входы ---------------------------------------------------------

    def submit(self, stimulus: Stimulus) -> None:
        if stimulus.kind == "control":
            self._apply_control(stimulus)
            return
        if stimulus.kind == "game_event" and not stimulus.payload.get("silent"):
            self._last_game_event_at = time.time()
        heapq.heappush(self._heap, (-int(stimulus.priority), next(self._counter), stimulus))
        self._wakeup.set()

    def mute_for(self, seconds: float) -> None:
        self.muted_until = time.time() + seconds
        log.info("Режиссёр замьючен на %.0f с", seconds)

    def _apply_control(self, stimulus: Stimulus) -> None:
        if stimulus.type == "mute":
            self.mute_for(float(stimulus.payload.get("seconds", 60)))

    # --- обработка -----------------------------------------------------

    def _rate_ok(self, now: float) -> bool:
        # Единственный регулятор темпа — глобальный кулдаун между репликами.
        return now - self._last_replica_at >= self.settings.global_cooldown_s

    def _debounce_hold(self, stimulus: Stimulus, now: float) -> bool:
        """Придержать мелкое событие, пока не уляжется буря событий.

        Дебаунс трогает только НЕкрупные (≤ NORMAL) игровые события: буря
        засветов/уронов схлопывается в одну реплику про самое важное. Крупные
        события (фраг/смерть/пожар/детонация — HIGH/CRITICAL) и заказы из чата
        проходят без задержки. `stimulus` — верхушка кучи (макс. приоритет), так
        что при ≤ NORMAL в очереди заведомо нет ничего важнее — держать безопасно.
        """
        if self.settings.debounce_s <= 0:
            return False
        if stimulus.kind != "game_event" or stimulus.payload.get("silent"):
            return False
        if stimulus.priority > Priority.NORMAL:
            return False
        # Держим, пока события всё ещё сыплются (пауза короче debounce_s)…
        bursting = now - self._last_game_event_at < self.settings.debounce_s
        # …но не дольше debounce_max_s, иначе в затяжном замесе замолчим совсем.
        within_cap = now - stimulus.created_at < self.settings.debounce_max_s
        return bursting and within_cap

    async def process_once(self) -> bool:
        """Обработать один стимул из очереди. True, если что-то сделали."""
        if not self._heap:
            return False
        now = time.time()
        if self._debounce_hold(self._heap[0][2], now):
            return False  # буря не улеглась — ждём, реплику пока не рождаем
        _, _, stimulus = heapq.heappop(self._heap)

        # Память обновляем всегда — даже если реплика не выйдет.
        facts = self.memory.register(stimulus)

        # Тихие события (§4.2): регистрируются в памяти, но реплику не рождают.
        if stimulus.payload.get("silent"):
            return True

        must_speak = (
            stimulus.kind == "game_event" and stimulus.type in ALWAYS_SPEAK_TYPES
        )

        if stimulus.expired(now):
            return True
        if now < self.muted_until:
            return True
        if not must_speak and not self._rate_ok(now):
            return True

        # Реплика отталкивается от текущего боя; сессия — редкая подколка.
        memory_lines = facts + self.memory.battle_lines()
        want_session = (
            stimulus.kind == "chat_order" and stimulus.type == "stats"
        ) or random.random() < self.SESSION_TEASE_PROB
        session_lines = self.memory.session_lines() if want_session else []
        prompt = build_prompt(stimulus, memory_lines, session_lines)
        text = await self.backend.generate(prompt)

        if text is None:
            if stimulus.kind == "chat_order" and stimulus.type == "dir":
                return True  # свободный заказ шаблоном не подменяем
            text = fallback_line(stimulus)

        # Реплика могла «протухнуть», пока генерировалась.
        if stimulus.expired():
            return True

        self._last_replica_at = time.time()
        self._replica_times.append(self._last_replica_at)
        try:
            await self.publish(text, stimulus)
        except Exception:  # шоу продолжается
            log.exception("publish failed")
        return True

    async def run(self) -> None:
        self._running = True
        while self._running:
            worked = await self.process_once()
            if not worked:
                self._wakeup.clear()
                # Если в очереди что-то придержано дебаунсом — просыпаемся чаще,
                # чтобы вовремя выпустить реплику, как только буря уляжется.
                timeout = 0.2 if self._heap else 1.0
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass

    def stop(self) -> None:
        self._running = False
        self._wakeup.set()

    def stats(self) -> dict:
        now = time.time()
        return {
            "queue_len": len(self._heap),
            "replicas_last_minute": len([t for t in self._replica_times if now - t < 60.0]),
            "muted_until": self.muted_until if self.muted_until > now else None,
        }
