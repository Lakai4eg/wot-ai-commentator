"""Память LoL: текущая игра (основа реплик) + сессия (редкие подколки).

Зеркало памяти WoT: та же пара масштабов и тот же интерфейс
register/battle_lines/session_lines/summary_lines.
"""

from __future__ import annotations

from collections import Counter

from ...stimulus import Stimulus


class LolBattleMemory:
    """Счётчики текущей игры; сбрасываются на battle_start."""

    def __init__(self, map_name: str | None = None, mode: str | None = None,
                 champion: str | None = None, position: str | None = None) -> None:
        self.map = map_name
        self.mode = mode
        self.champion = champion
        self.position = position
        self.kills = 0
        self.deaths = 0
        self.assists = 0
        self.multikills: list[str] = []
        self.objectives: Counter[str] = Counter()  # взятые командой стримера
        self.lost_objectives = 0
        self.turrets = 0
        self.inhibs = 0
        self.low_hp_events = 0
        self.killers: Counter[str] = Counter()
        # Последняя таблица союзников из team_state: {champion, kills, deaths}.
        self.allies: list[dict] = []

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.champion:
            out.append(f"чемпион стримера: {self.champion}")
        if self.mode:
            out.append(f"режим: {self.mode}")
        if self.kills or self.deaths or self.assists:
            out.append(f"счёт стримера: {self.kills}/{self.deaths}/{self.assists}")
        if self.multikills:
            out.append("мультикиллы за игру: " + ", ".join(self.multikills))
        if self.objectives:
            out.append("объекты команды: "
                       + ", ".join(f"{k} ×{v}" for k, v in self.objectives.items()))
        if self.lost_objectives:
            out.append(f"объектов отдано противнику: {self.lost_objectives}")
        if self.turrets:
            out.append(f"башен добито стримером: {self.turrets}")
        if self.inhibs:
            out.append(f"ингибиторов снесено стримером: {self.inhibs}")
        if self.low_hp_events:
            out.append(f"был на волоске: {self.low_hp_events} раз(а)")
        notable: list[str] = []
        for a in self.allies:
            kills = int(a.get("kills") or 0)
            deaths = int(a.get("deaths") or 0)
            champ = a.get("champion") or "союзник"
            if kills >= 6:
                notable.append(f"союзник {champ}: {kills}/{deaths} — тащит")
            elif deaths >= 4:
                notable.append(f"союзник {champ}: {kills}/{deaths} — фидит")
        out.extend(notable[:2])  # не больше двух строк — контекст, не простыня
        top = self.killers.most_common(1)
        if top and top[0][1] >= 2:
            out.append(f"главный обидчик в игре: «{top[0][0]}» ({top[0][1]} смертей)")
        return out


class LolSessionMemory:
    def __init__(self) -> None:
        self.battle = LolBattleMemory()
        self.games = 0
        self.wins = 0
        self.kills = 0
        self.deaths = 0
        self.assists = 0
        self.multikills: Counter[str] = Counter()
        self.pentas = 0
        self.first_bloods = 0
        self.deaths_by_champion: Counter[str] = Counter()

    def register(self, stimulus: Stimulus) -> list[str]:
        """Обновляет оба масштаба; возвращает контекст-факты для промпта."""
        facts: list[str] = []
        t, p, b = stimulus.type, stimulus.payload, self.battle

        if t == "battle_start":
            self.battle = LolBattleMemory(
                map_name=p.get("map"), mode=p.get("mode"), champion=p.get("champion"),
                position=p.get("position"),
            )
        elif t == "frag":
            self.kills += 1
            b.kills += 1
            if b.kills >= 5:
                facts.append(f"у стримера уже {b.kills} убийств за игру")
        elif t == "death":
            self.deaths += 1
            b.deaths += 1
            killer = str(p.get("killer") or "").strip()
            if killer and killer != "неизвестный":
                b.killers[killer] += 1
                self.deaths_by_champion[killer] += 1
                n = self.deaths_by_champion[killer]
                if n >= 2:
                    facts.append(f"это уже {n}-я смерть от «{killer}» за сессию")
            if b.deaths >= 5:
                facts.append(f"смертей за игру уже {b.deaths} — в чате это зовут «фид»")
        elif t == "assist":
            self.assists += 1
            b.assists += 1
        elif t == "multikill":
            label = str(p.get("label") or "мультикилл")
            b.multikills.append(label)
            self.multikills[label] += 1
            if int(p.get("count") or 0) >= 5:
                self.pentas += 1
                facts.append(
                    f"ПЕНТАКИЛЛ! Уже {self.pentas}-й за сессию" if self.pentas > 1
                    else "ПЕНТАКИЛЛ — высшее достижение, случается раз в сто игр"
                )
        elif t == "first_blood":
            if p.get("by_me"):
                self.first_bloods += 1
                facts.append("первая кровь матча — за стримером")
        elif t == "objective":
            side = p.get("side")
            if side == "ours":
                b.objectives[str(p.get("kind") or "объект")] += 1
            elif side == "theirs":
                b.lost_objectives += 1
            # unknown — сторона не определена, не приписываем никому
        elif t == "turret":
            b.turrets += 1
        elif t == "inhib":
            b.inhibs += 1
        elif t == "low_hp":
            b.low_hp_events += 1
        elif t == "team_state":
            b.allies = list(p.get("allies") or [])
        elif t == "battle_result":
            self.games += 1
            if p.get("outcome") == "win":
                self.wins += 1
        return facts

    _POSITION_RU = {"TOP": "топ", "JUNGLE": "лес", "MIDDLE": "мид",
                    "BOTTOM": "адк", "UTILITY": "саппорт"}

    def brief_subject(self) -> str | None:
        """Тема брифа: «Yasuo, мид». Чемпион неизвестен — None."""
        champion = self.battle.champion
        if not champion:
            return None
        role = self._POSITION_RU.get(self.battle.position or "")
        return f"{champion}, {role}" if role else champion

    def battle_lines(self) -> list[str]:
        """Контекст текущей игры — основа каждой реплики."""
        return self.battle.lines()

    def session_lines(self) -> list[str]:
        """Итоги сессии — для редких подколок и команды !stats."""
        lines: list[str] = []
        if self.games:
            lines.append(f"игр за сессию: {self.games}, побед: {self.wins}")
        if self.kills or self.deaths or self.assists:
            lines.append(f"суммарный счёт за сессию: {self.kills}/{self.deaths}/{self.assists}")
        if self.pentas:
            lines.append(f"пентакиллов за сессию: {self.pentas}")
        if self.multikills:
            lines.append("мультикиллы за сессию: "
                         + ", ".join(f"{k} ×{v}" for k, v in self.multikills.items()))
        top = self.deaths_by_champion.most_common(1)
        if top and top[0][1] >= 2:
            lines.append(f"главный обидчик сессии: «{top[0][0]}» ({top[0][1]} смертей)")
        if self.first_bloods:
            lines.append(f"первых кровей за сессию: {self.first_bloods}")
        return lines

    def summary_lines(self) -> list[str]:
        """Полная сводка (панель/статус): игра + сессия."""
        return self.battle_lines() + self.session_lines()
