"""LoL-колорит: описания событий, сленг-блок для промпта."""

from __future__ import annotations

from ...stimulus import Stimulus

_FLAVOR = (
    "Игра на стриме — League of Legends.\n"
    "- Мишень — тот, кто дал повод: стример, тиммейты («тиммейты виноваты» — "
    "вечная классика), джунглер, который «опять не ганкает», фидер, противники — "
    "прожарка без поблажек любому.\n"
    "- Сленг LoL умеренно: ферма, ганк, вард, пуш, фид, скейлинг — но так, "
    "чтобы шутку понял и новичок. Реагируй на то, что реально происходит, "
    "без заученных мемов про конкретных чемпионов."
    "\n- Не путай, кто кого убил: в описании события всегда названы убийца и "
    "жертва. Если умер союзник или противник — стримера не хорони; прожаривай "
    "ровно тех, кто назван."
)

_EVENT_DESCRIPTIONS = {
    "battle_start": (
        "Матч на Ущелье призывателей только начался — стример выходит за {champion_ru}. "
        "Поприветствуй зрителей и задай тон стриму — коротко, с иронией."
    ),
    "frag": "Стример убил вражеского чемпиона {target}.",
    "death": "Стримера убил {killer}.",
    "assist": (
        "Стример записал ассист — помог{assist_killer_ru} убить {target}. "
        "Сам стример жив и лично никого не убил."
    ),
    "multikill": "Стример собрал {label} ({count} убийства подряд)!",
    "first_blood": "Первая кровь матча: {note}.",
    "objective": "{side_ru}: {kind}.{stolen_note}",
    "turret": "Стример добил вражескую башню.",
    "turret_ours": (
        "Команда стримера снесла башню противника — добил союзник или миньоны, "
        "лично стример её не добивал."
    ),
    "turret_theirs": "Противник снёс башню команды стримера.",
    "inhib": "Стример снёс ингибитор противника.",
    "ace": "{ace_ru}",
    "battle_result": "Игра окончена: {outcome_ru}.",
    "ally_feeding": (
        "Союзник стримера на чемпионе {champion} набрал уже {deaths} смертей. "
        "Прожарь союзника-«кормильца» команды — мишень он, не стример."
    ),
    "ally_carrying_multikill": (
        "Союзник {champion} собрал {label}! Союзнику — ядовитое уважение, "
        "стримеру — прожарка: пока он смотрел, играли за него."
    ),
    "ally_carrying_lead": (
        "Союзник {champion} набрал {kills} убийств против {my_kills} у стримера. "
        "Союзнику — ядовитое уважение, стримера прожарь: кто-то тащит вместо него."
    ),
    "team_gap_spectator": (
        "Игра идёт уже больше десяти минут, у команды {team_kills} убийств, "
        "а у стримера всё ещё 0/0/0. Подколи стримера-наблюдателя."
    ),
    "team_gap_behind": (
        "Команда стримера отстаёт от противника на {diff} убийств. "
        "Прожарь всю команду разом."
    ),
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


def flavor_lines() -> str:
    return _FLAVOR


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


def describe_event(stimulus: Stimulus) -> str:
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
    p["stolen_note"] = (" Объект УКРАДЕН из-под носа — драма!" if p.get("stolen") else "")
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
    p["outcome_ru"] = "победа" if p.get("outcome") == "win" else "поражение"
    key = variant_key(stimulus)
    template = (_EVENT_DESCRIPTIONS.get(key)
                or _EVENT_DESCRIPTIONS.get(stimulus.type, f"Событие: {stimulus.type}."))
    return template.format_map(p)
