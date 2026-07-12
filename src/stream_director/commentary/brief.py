"""Генерация игрового брифа: LLM пишет подсказку под технику/чемпиона.

Бриф — вторая половина игрового промпта: база игры статична, а бриф
привязан к тому, на чём стример играет прямо сейчас. Генерируется на старте
боя в фоне; пока его нет, комментатор работает на одной базе.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..db import PromptStore
from ..games.base import GameModule
from .base import CommentaryBackend
from .defaults import PERSONA_BUILTIN

log = logging.getLogger(__name__)

BRIEF_MAX_TOKENS = 700
BRIEF_TIMEOUT_S = 25.0

_META_PROMPT = """Ты — редактор закадрового комментатора игрового стрима.
Персона комментатора:
{persona}

Игра: {game}.
Стример играет на: {subject}.

Напиши для комментатора БРИФ на 5–8 пунктов — фактуру и углы для шуток именно
про это. Что должно быть в брифе:
- роль этого {unit_ru} в бою: что от него ждут, что он умеет и чего не умеет;
- репутация в сообществе игроков: за что его любят, за что ненавидят, какие
  штампы с ним связаны;
- за что прожаривать стримера именно на нём: типичные ошибки и позорные ситуации;
{extra}

Формат: маркированный список, каждый пункт — одна строка. Только фактура и
углы для шуток. НЕ пиши готовых реплик, не обращайся к стримеру, не шути сам."""

_EXTRA = {
    "wot": ("- как язвить, когда стример получает урон от каждого класса техники "
            "(ЛТ, СТ, ТТ, ПТ, САУ) — по строке на класс."),
    "lol": ("- как реагировать на смерти, киллы и объекты именно на этом чемпионе: "
            "что тут считается провалом, а что — ожидаемым."),
}
_UNIT_RU = {"wot": "танка", "lol": "чемпиона"}


class BriefGenerator:
    def __init__(self, backend: CommentaryBackend, store: PromptStore,
                 settings: Settings) -> None:
        self.backend = backend
        self.store = store
        self.settings = settings
        self.last_error: dict[str, str | None] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(self, module: GameModule) -> None:
        """Запустить генерацию в фоне. Повторный вызов при живой задаче — no-op."""
        task = self._tasks.get(module.id)
        if task is not None and not task.done():
            return
        self._tasks[module.id] = asyncio.get_running_loop().create_task(
            self._guarded(module)
        )

    async def _guarded(self, module: GameModule) -> None:
        try:
            await self.generate(module)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Генерация брифа для «%s» упала", module.id)
            self.last_error[module.id] = "внутренняя ошибка, см. лог"

    async def generate(self, module: GameModule) -> str | None:
        subject = module.brief_subject()
        if not subject:
            self.last_error[module.id] = "не знаю, на чём играет стример"
            return None
        persona = self.store.active_persona_text(self.settings.active_persona_id)
        prompt = _META_PROMPT.format(
            persona=persona or PERSONA_BUILTIN,
            game=module.display_name,
            subject=subject,
            unit_ru=_UNIT_RU.get(module.id, "персонажа"),
            extra=_EXTRA.get(module.id, ""),
        )
        text = await self.backend.generate(
            prompt, max_tokens=BRIEF_MAX_TOKENS, timeout_s=BRIEF_TIMEOUT_S
        )
        if not text:
            # Старый бриф не трогаем: он про другую технику — но и не используем,
            # если тема сменилась (сверка по subject в director).
            self.last_error[module.id] = self.backend.last_error or "пустой ответ"
            log.warning("Бриф для «%s» не сгенерирован: %s",
                        module.id, self.last_error[module.id])
            return None
        self.store.save_brief(module.id, subject, text.strip())
        self.last_error[module.id] = None
        log.info("Бриф для «%s» готов (%s)", module.id, subject)
        return text
