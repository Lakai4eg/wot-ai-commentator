"""Директор: приоритетная очередь стимулов → LLM → публикация реплик."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import random
import time
from collections import deque
from typing import Awaitable, Callable

from .commentary.base import CommentaryBackend
from .commentary.brief import BriefGenerator
from .commentary.defaults import RESPONSE_FORMAT_KEY, game_base_key
from .commentary.prompts import PromptContext, build_event_prompt, build_order_prompt
from .config import Settings
from .db import PromptStore
from .stimulus import Priority, Stimulus
from .games.base import ActiveGameTracker, GameModule

log = logging.getLogger(__name__)

PublishFn = Callable[[str, Stimulus], Awaitable[None]]


class Director:
    # Как часто в промпт попадают итоги сессии (редкие подколки поверх
    # контекста текущего боя).
    SESSION_TEASE_PROB = 0.2

    def __init__(
        self,
        settings: Settings,
        backend: CommentaryBackend,
        publish: PublishFn,
        tracker: ActiveGameTracker,
        store: PromptStore,
        briefs: BriefGenerator | None = None,
    ):
        self.settings = settings
        self.backend = backend
        self.publish = publish
        self.tracker = tracker
        self.store = store
        self.briefs = briefs
        self.games: dict[str, GameModule] = {}

        self._heap: list[tuple[int, int, Stimulus]] = []
        self._counter = itertools.count()
        self._last_replica_at = 0.0
        # Окно склейки: копим игровые события и факты памяти, пока окно открыто.
        self._batch: list[Stimulus] = []
        self._batch_facts: list[str] = []
        self._batch_opened_at = 0.0
        self._replica_times: list[float] = []
        # Последние озвученные реплики — в промпт, чтобы LLM не повторялась.
        self._recent_replicas: deque[str] = deque(maxlen=8)
        self._wakeup = asyncio.Event()
        self._running = False

    # --- входы ---------------------------------------------------------

    def register(self, module: GameModule) -> None:
        self.games[module.id] = module

    def _module_for(self, stimulus: Stimulus) -> GameModule:
        """Модуль по игре стимула; чат-заказы и неизвестное — активная игра."""
        game_id = stimulus.game or self.tracker.active
        return self.games.get(game_id) or self.games[self.tracker.active]

    def submit(self, stimulus: Stimulus) -> None:
        heapq.heappush(self._heap, (-int(stimulus.priority), next(self._counter), stimulus))
        self._wakeup.set()

    # --- обработка -----------------------------------------------------

    def _rate_ok(self, now: float) -> bool:
        # Единственный регулятор темпа — глобальный кулдаун между репликами.
        return now - self._last_replica_at >= self.settings.global_cooldown_s

    def _prompt_context(self, module: GameModule, memory_lines: list[str],
                        session_lines: list[str]) -> PromptContext:
        base = self.store.get_prompt(game_base_key(module.id))
        brief = self.store.get_brief(module.id)
        game_prompt = base
        # Бриф с чужой техники не годится: он про другой танк/чемпиона. Но если
        # тема неизвестна с любой стороны (бой не начался, бриф правлен руками
        # до первой генерации) — сверять не с чем, берём как есть.
        subject = module.brief_subject()
        if (brief and brief.text.strip()
                and (not subject or not brief.subject or brief.subject == subject)):
            game_prompt = (f"{base}\n\nБриф под то, на чём играет стример "
                           f"({brief.subject}):\n{brief.text}")
        angles = module.joke_angles() if module.joke_angles else ()
        return PromptContext(
            persona=self.store.active_persona_text(self.settings.active_persona_id),
            response_format=self.store.get_prompt(RESPONSE_FORMAT_KEY),
            game_prompt=game_prompt,
            memory_lines=memory_lines,
            session_lines=session_lines,
            recent_lines=list(self._recent_replicas),
            joke_angles=angles,
        )

    async def process_once(self) -> bool:
        """Разобрать очередь и, если окно склейки закрылось, сказать реплику."""
        now = time.time()
        worked = False

        # 1. Разбираем кучу: чат-заказ отрабатываем сразу, игровые события копим.
        while self._heap:
            _, _, stimulus = heapq.heappop(self._heap)
            module = self._module_for(stimulus)
            facts = module.memory.register(stimulus)  # память обновляем всегда

            if stimulus.type == "battle_start" and self.briefs is not None:
                # Старт боя: техника/чемпион уже в памяти — просим LLM бриф.
                self.briefs.schedule(module)

            if stimulus.kind == "chat_order":
                # Заказ зрителя не сливается с игровыми событиями и не ждёт окна.
                await self._speak_order(stimulus, module, facts)
                return True

            # Тихие события (§4.2): регистрируются в памяти, но реплику не рождают.
            if stimulus.payload.get("silent"):
                worked = True
                continue

            if not self._batch:
                self._batch_opened_at = now
            self._batch.append(stimulus)
            self._batch_facts.extend(facts)
            worked = True

        if not self._batch:
            return worked

        # 2. Решаем, пора ли говорить.
        module = self._module_for(self._batch[0])
        must_speak = any(
            s.priority >= Priority.CRITICAL or s.type in module.always_speak_types
            for s in self._batch
        )
        window_over = now - self._batch_opened_at >= self.settings.debounce_window_s
        if not must_speak and not window_over:
            return worked  # окно ещё открыто — копим дальше
        if not must_speak and not self._rate_ok(now):
            return worked  # кулдаун — продолжаем копить, события не теряем

        batch = [s for s in self._batch if not s.expired(now)]
        facts = self._batch_facts
        self._batch, self._batch_facts = [], []
        if not batch:
            return True  # всё протухло, пока ждали

        await self._speak_batch(batch, module, facts)
        return True

    # --- реплики -------------------------------------------------------

    def _lead(self, batch: list[Stimulus]) -> Stimulus:
        """Главное событие пачки: самое важное, при равной важности — свежее."""
        return max(batch, key=lambda s: (int(s.priority), s.created_at))

    def _memory_lines(self, module: GameModule,
                      facts: list[str]) -> tuple[list[str], list[str]]:
        # Реплика отталкивается от текущего боя; сессия — редкая подколка.
        want_session = random.random() < self.SESSION_TEASE_PROB
        session = module.memory.session_lines() if want_session else []
        return facts + module.memory.battle_lines(), session

    async def _speak_batch(self, batch: list[Stimulus], module: GameModule,
                           facts: list[str]) -> None:
        memory_lines, session_lines = self._memory_lines(module, facts)
        ctx = self._prompt_context(module, memory_lines, session_lines)
        # Каждое событие описывает СВОЙ модуль: в окно склейки может попасть
        # хвост предыдущей игры, если активная сменилась прямо посреди окна.
        events = [self._module_for(s).build_event(s) for s in batch]
        prompt = build_event_prompt(ctx, events, self.settings.debounce_window_s)
        text = await self.backend.generate(prompt)
        if text is None:
            return  # LLM молчит — реплики нет, ошибка видна в бейдже панели
        lead = self._lead(batch)
        if lead.expired():
            return  # реплика протухла, пока генерировалась
        await self._emit_replica(text, lead)

    async def _speak_order(self, stimulus: Stimulus, module: GameModule,
                           facts: list[str]) -> None:
        if stimulus.expired():
            return
        memory_lines, session_lines = self._memory_lines(module, facts)
        ctx = self._prompt_context(module, memory_lines, session_lines)
        prompt = build_order_prompt(
            ctx,
            str(stimulus.payload.get("text", "")),
            str(stimulus.payload.get("username", "зритель")),
        )
        text = await self.backend.generate(prompt)
        if text is None:
            return
        await self._emit_replica(text, stimulus)

    async def _emit_replica(self, text: str, stimulus: Stimulus) -> None:
        self._recent_replicas.append(text)
        self._last_replica_at = time.time()
        # Держим только последнюю минуту — иначе список растёт весь стрим.
        self._replica_times = [
            t for t in self._replica_times if self._last_replica_at - t < 60.0
        ]
        self._replica_times.append(self._last_replica_at)
        try:
            await self.publish(text, stimulus)
        except Exception:  # шоу продолжается
            log.exception("publish failed")

    async def run(self) -> None:
        self._running = True
        while self._running:
            worked = await self.process_once()
            if not worked:
                self._wakeup.clear()
                # Пока окно склейки открыто — просыпаемся чаще, чтобы выпустить
                # реплику сразу, как только оно закроется.
                timeout = 0.2 if (self._heap or self._batch) else 1.0
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
            "batch_len": len(self._batch),
            "replicas_last_minute": len([t for t in self._replica_times if now - t < 60.0]),
        }
