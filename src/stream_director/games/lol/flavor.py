"""LoL-колорит: перевод стимулов в GameEvent (факты + мишень)."""

from __future__ import annotations

from ...commentary.events import GameEvent
from ...stimulus import Stimulus

# ключ (из variant_key) → (шаблон факта, кого прожаривать)
_HEADLINES = {
    "battle_start": ("Матч на Ущелье призывателей начался — стример выходит "
                     "за {champion_ru}.", "streamer"),
    "frag": ("Стример убил вражеского чемпиона {target}.", "enemy"),
    "death": ("Стримера убил {killer}.", "streamer"),
    "assist": ("Стример записал ассист — помог{assist_killer_ru} убить {target}. "
               "Сам стример жив и лично никого не убил.", "enemy"),
    "multikill": ("Стример собрал {label} ({count} убийства подряд).", "streamer"),
    "first_blood": ("Первая кровь матча: {note}.", "none"),
    "objective": ("{side_ru}: {kind}.", "none"),
    "turret": ("Стример добил вражескую башню.", "enemy"),
    "turret_ours": ("Команда стримера снесла башню противника — добил союзник "
                    "или миньоны, лично стример её не добивал.", "team"),
    "turret_theirs": ("Противник снёс башню команды стримера.", "team"),
    "inhib": ("Стример снёс ингибитор противника.", "enemy"),
    "ace": ("{ace_ru}", "none"),
    "ally_feeding": ("Союзник стримера на чемпионе {champion} набрал уже "
                     "{deaths} смертей.", "ally"),
    "ally_carrying_multikill": ("Союзник {champion} собрал {label}.", "streamer"),
    "ally_carrying_lead": ("Союзник {champion} набрал {kills} убийств против "
                           "{my_kills} у стримера.", "streamer"),
    "team_gap_spectator": ("Игра идёт больше десяти минут, у команды {team_kills} "
                           "убийств, а у стримера всё ещё 0/0/0.", "streamer"),
    "team_gap_behind": ("Команда стримера отстаёт от противника на {diff} убийств.", "team"),
}


def variant_key(stimulus: Stimulus) -> str:
    """Ключ шаблона/описания: часть событий ветвится по payload."""
    t, p = stimulus.type, stimulus.payload
    if t == "ally_carrying":
        return "ally_carrying_multikill" if p.get("label") else "ally_carrying_lead"
    if t == "team_gap":
        return f"team_gap_{p.get('kind', 'behind')}"
    if t == "objective" and p.get("side") in ("ours", "theirs"):
        side = p["side"]
        if p.get("stolen"):
            return f"objective_stolen_{side}"  # крад важнее вида объекта
        kind_key = p.get("kind_key")
        if kind_key in ("dragon", "baron", "herald"):
            return f"objective_{kind_key}_{side}"
        return f"objective_{side}"  # старый payload: пул откатится на objective
    if t == "turret" and p.get("side") in ("ours", "theirs"):
        return f"turret_{p['side']}"
    if t == "ace":
        return "ace_theirs" if p.get("side") == "theirs" else "ace_ours"
    return t


_JOKE_ANGLES = (
    "подколи союзника, если контекст даёт повод (фидер, керри, джунглер)",
    "злорадство над тем, кому только что не повезло",
    "обыграй текущий счёт стримера",
    "бытовая метафора: сравни происходящее с чем-то из обычной жизни",
    "пафос киберспортивного кастера — с лёгким перегибом",
    "«чат уже печатает» — представь реакцию зрителей",
    "обыграй чемпиона противника или его судьбу",
    "похвала с подвохом: комплимент, который смешит",
    "сухая констатация факта — юмор в невозмутимости",
    "обыграй ферму, варды или макро-детали",
)


def joke_angles() -> tuple[str, ...]:
    return _JOKE_ANGLES


def build_event(stimulus: Stimulus) -> GameEvent:
    p = dict(stimulus.payload)
    p.setdefault("target", "противника")
    p.setdefault("killer", "противник")
    p.setdefault("label", "мультикилл")
    p.setdefault("count", "?")
    p.setdefault("kind", "объект")
    p["champion_ru"] = p.get("champion") or "своего чемпиона"
    p.setdefault("champion", "союзник")
    p.setdefault("deaths", "?")
    p.setdefault("kills", "?")
    p.setdefault("my_kills", "?")
    p.setdefault("team_kills", "?")
    p.setdefault("diff", "?")
    side = p.get("side", "ours")
    p["side_ru"] = {
        "ours": "Команда стримера забрала объект",
        "theirs": "Противник забрал объект",
    }.get(side, "На карте взяли объект")  # unknown — не приписываем сторону
    # assist: добивший союзник — из payload события, не из setdefault killer.
    ally_killer = stimulus.payload.get("killer") if stimulus.type == "assist" else None
    p["assist_killer_ru"] = f" союзнику {ally_killer}" if ally_killer else ""
    # first_blood: явные стороны и жертва — ЛЛМ не должна гадать, кто умер.
    victim = p.get("victim")
    victim_part = (", убив самого стримера" if p.get("victim_me")
                   else f", убив {victim}" if victim else "")
    actor = p.get("actor", "кто-то")
    if p.get("by_me"):
        p["note"] = f"её забрал сам стример{victim_part}"
    elif p.get("side") == "ours":
        p["note"] = (f"её забрал союзник {actor}{victim_part} — "
                     "заслуга союзника, не стримера")
    elif p.get("side") == "theirs":
        p["note"] = (f"её забрал противник {actor}{victim_part}"
                     + ("" if p.get("victim_me")
                        else " — сам стример жив, его не хорони"))
    else:
        p["note"] = f"её забрал {actor}{victim_part}"
    p["ace_ru"] = ("Команда стримера оформила эйс — вся вражеская пятёрка мертва."
                   if side == "ours"
                   else "Эйс у противника — вся команда стримера полегла.")

    key = variant_key(stimulus)
    template, roast = (_HEADLINES.get(key)
                       or _HEADLINES.get(stimulus.type,
                                         (f"Событие: {stimulus.type}.", "none")))
    facts: list[str] = []

    # Ветвящиеся события: мишень зависит от стороны, а не только от типа.
    if key.startswith("objective_"):
        roast = "enemy" if p.get("side") == "ours" else "team"
        if p.get("stolen"):
            facts.append("объект УКРАДЕН из-под носа — драма")
    elif key == "ace_ours":
        roast = "enemy"
    elif key == "ace_theirs":
        roast = "team"
    elif stimulus.type == "first_blood":
        if p.get("by_me") or p.get("side") == "ours":
            roast = "enemy"
        elif p.get("victim_me"):
            roast = "streamer"
        else:
            roast = "ally"
    elif key == "ally_carrying_multikill":
        facts.append("союзнику — ядовитое уважение, стримеру — прожарка: "
                     "пока он смотрел, играли за него")
    elif key == "ally_carrying_lead":
        facts.append("кто-то тащит вместо стримера")

    return GameEvent(
        type=key,
        headline=template.format_map(p),
        roast_target=roast,
        side=str(p.get("side") or "neutral"),
        # actor/target — из исходного payload: подставленные заглушки тут не нужны.
        actor=stimulus.payload.get("actor") or stimulus.payload.get("champion"),
        target=stimulus.payload.get("target"),
        facts=facts,
        importance=stimulus.priority,
    )
