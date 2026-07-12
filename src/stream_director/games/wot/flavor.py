"""WoT-колорит: перевод стимулов в GameEvent (факты + мишень)."""

from __future__ import annotations

from ...commentary.events import GameEvent
from ...stimulus import Stimulus

# type → (шаблон факта, кого прожаривать)
_HEADLINES = {
    "frag": ("Стример уничтожил противника {target}.", "enemy"),
    "death": ("Стримера уничтожили. Убийца: {killer}.", "streamer"),
    "damage_dealt": ("Стример нанёс {amount} урона по {target}.", "enemy"),
    "damage_received": ("Стример получил {amount} урона от {source}.", "streamer"),
    "crit": ("Стример пробил критическое повреждение противнику.", "enemy"),
    "spotted": ("Стример засветил противника — теперь его видит вся команда.", "streamer"),
    "assist": ("Стример помог союзникам разведкой или сетапом на {amount} урона.", "streamer"),
    "blocked": ("Броня стримера отразила {amount} урона.", "streamer"),
    "fire": ("Танк стримера горит — надо срочно тушить.", "streamer"),
    "damage_milestone": ("Суммарный урон стримера за бой достиг {total}.", "streamer"),
    "tier11": ("Стример выехал на танке 11 уровня ({tank}).", "streamer"),
    "base_capture": ("Идёт захват базы: {side_ru}.", "team"),
}


def build_event(stimulus: Stimulus) -> GameEvent:
    p = dict(stimulus.payload)
    p.setdefault("target", "противника")
    p.setdefault("killer", "неизвестный")
    p.setdefault("amount", "?")
    p.setdefault("source", "противник")
    p.setdefault("total", "?")
    p.setdefault("tank", "танк")
    p["side_ru"] = (
        "союзники стримера встали на базу противника"
        if p.get("side") == "ours"
        else "противник встал на базу команды стримера"
    )

    template, roast = _HEADLINES.get(stimulus.type, (f"Событие: {stimulus.type}.", "none"))
    facts: list[str] = []

    if stimulus.type == "damage_received":
        source_class = p.get("source_class")
        if p.get("from_arta") or source_class == "САУ":
            facts.append("прилёт от АРТЫ из-за горизонта — арту в танковом "
                         "сообществе принято язвительно недолюбливать")
        elif source_class:
            facts.append(f"урон прилетел от {source_class}")
    elif stimulus.type == "death":
        if p.get("killer_class"):
            facts.append(f"убийца — {p['killer_class']}")
    elif stimulus.type == "tier11":
        facts.append("танки 11 уровня в сообществе принято дружно недолюбливать: "
                     "инфляция уровней, побор, «кто их вообще просил»")
    elif stimulus.type == "base_capture":
        facts.append("базу захватывает команда, лично стример может быть не при делах")

    return GameEvent(
        type=stimulus.type,
        headline=template.format_map(p),
        roast_target=roast,
        side=str(p.get("side") or "neutral"),
        actor=p.get("source") if stimulus.type == "damage_received" else None,
        target=p.get("target"),
        facts=facts,
        importance=stimulus.priority,
    )
