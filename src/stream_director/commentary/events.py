"""Стандартизованное описание игрового события для LLM.

Модуль игры отдаёт факты (что произошло, кто дал повод), а не указания тону —
иначе склейка нескольких событий в один промпт превращается в набор
противоречивых команд («подколи стримера» + «прожарь всю команду»).
Тон задают персона и формат ответа, мишень — поле roast_target.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..stimulus import Priority

# Сколько событий пачки показываем поимённо; остальные — счётчиком.
MAX_BATCH_LINES = 5

ROAST_TARGETS_RU = {
    "streamer": "стримера",
    "ally": "союзника",
    "enemy": "противника",
    "team": "всю команду стримера",
    "none": "никого конкретно — просто опиши момент",
}


@dataclass
class GameEvent:
    type: str  # ключ события (с учётом вариантов: objective_dragon_ours)
    headline: str  # один факт: «Стримера убил Yasuo»
    roast_target: str = "streamer"  # streamer | ally | enemy | team | none
    side: str = "neutral"  # ours | theirs | neutral
    actor: str | None = None
    target: str | None = None
    facts: list[str] = field(default_factory=list)  # «прилёт от САУ», «объект украден»
    importance: Priority = Priority.NORMAL


def _roast_ru(target: str) -> str:
    return ROAST_TARGETS_RU.get(target, "того, кто дал повод")


def render_event(event: GameEvent) -> str:
    lines = ["Только что в игре:", f"— {event.headline}"]
    lines.extend(f"  · {fact}" for fact in event.facts)
    lines.append(f"Кого прожарить: {_roast_ru(event.roast_target)}.")
    lines.append("Отреагируй на это событие.")
    return "\n".join(lines)


def render_batch(events: list[GameEvent], window_s: float) -> str:
    """Пачка событий → один блок промпта. Одно событие рендерится как одиночное."""
    if len(events) == 1:
        return render_event(events[0])
    # sorted устойчив: внутри равной важности сохраняется порядок прихода.
    ordered = sorted(events, key=lambda e: -int(e.importance))
    shown, hidden = ordered[:MAX_BATCH_LINES], ordered[MAX_BATCH_LINES:]
    lines = [f"Что произошло за последние {window_s:.0f} с (важное сверху):"]
    for i, event in enumerate(shown, 1):
        mark = "ГЛАВНОЕ: " if i == 1 else ""
        facts = f" ({'; '.join(event.facts)})" if event.facts else ""
        lines.append(f"{i}. {mark}{event.headline}{facts}")
    if hidden:
        lines.append(f"…и ещё {len(hidden)} мелких событий.")
    lines.append(f"Главная мишень: {_roast_ru(shown[0].roast_target)}.")
    lines.append(
        "Отреагируй ОДНОЙ репликой — прежде всего про главное; "
        "мелочь можно задеть одним штрихом, а можно и не трогать."
    )
    return "\n".join(lines)
