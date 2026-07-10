"""Память: текущий бой (основа для реплик) + сессия (редкие подколки).

Реплики режиссёра отталкиваются от происходящего в бою — карта, танк,
счётчики этого боя. Сессионные итоги (боёв/побед, суммарные цифры) идут
в промпт лишь изредка, чтобы подтрунивать над общими результатами.
"""

from __future__ import annotations

from collections import Counter

from .events import Stimulus


class BattleMemory:
    """Счётчики текущего боя; сбрасываются на battle_start."""

    def __init__(self, map_name: str | None = None, mode: str | None = None,
                 tank: str | None = None) -> None:
        self.map = map_name
        self.mode = mode
        self.tank = tank
        self.frags = 0
        self.damage_dealt = 0
        self.damage_received = 0
        self.crits = 0
        self.spots = 0
        self.fires = 0
        self.assist_total = 0
        self.blocked_total = 0
        self.low_hp = False
        self.dead = False
        self.killer: str | None = None

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.map:
            out.append(f"карта: {self.map}")
        if self.tank:
            out.append(f"танк стримера: {self.tank}")
        if self.damage_dealt:
            out.append(f"урон за бой: {self.damage_dealt}")
        if self.frags:
            out.append(f"фрагов за бой: {self.frags}")
        if self.damage_received:
            out.append(f"получено урона за бой: {self.damage_received}")
        if self.assist_total:
            out.append(f"ассист за бой: {self.assist_total}")
        if self.blocked_total:
            out.append(f"заблокировано бронёй за бой: {self.blocked_total}")
        if self.crits:
            out.append(f"критов за бой: {self.crits}")
        if self.spots:
            out.append(f"засветов за бой: {self.spots}")
        if self.fires:
            out.append(f"горел в этом бою: {self.fires} раз(а)")
        if self.low_hp and not self.dead:
            out.append("прочность на исходе — стример на волоске")
        if self.dead:
            killer = f" (убийца: {self.killer})" if self.killer else ""
            out.append(f"стример уже уничтожен в этом бою{killer}")
        return out


class SessionMemory:
    def __init__(self) -> None:
        self.battle = BattleMemory()
        # Сессионные итоги — копятся между боями.
        self.deaths = 0
        self.frags = 0
        self.ammo_racks = 0
        self.battles = 0
        self.wins = 0
        self.damage_record = 0
        self.damage_dealt = 0
        self.damage_received = 0
        self.deaths_by_killer: Counter[str] = Counter()
        self.crits = 0
        self.spots = 0
        self.fires = 0
        self.assist_total = 0
        self.assist_count = 0
        self.blocked_total = 0
        self.blocked_count = 0
        self.low_hp_events = 0
        self.current_tank: str | None = None

    def register(self, stimulus: Stimulus) -> list[str]:
        """Обновляет оба масштаба; возвращает контекст-факты для промпта."""
        facts: list[str] = []
        t = stimulus.type
        p = stimulus.payload
        b = self.battle

        if t == "frag":
            self.frags += 1
            b.frags += 1
            if b.frags >= 3:
                facts.append(f"это уже {b.frags}-й фраг за бой")
        elif t == "death":
            self.deaths += 1
            b.dead = True
            killer = str(p.get("killer") or "").strip()
            if killer:
                b.killer = killer
                self.deaths_by_killer[killer] += 1
                n = self.deaths_by_killer[killer]
                if n >= 2:
                    facts.append(f"это уже {n}-я смерть от «{killer}» за сессию")
            if self.deaths >= 3:
                facts.append(f"всего смертей за сессию: {self.deaths}")
        elif t == "ammo_rack":
            self.ammo_racks += 1
            if self.ammo_racks >= 2:
                facts.append(f"боеукладка взрывается уже {self.ammo_racks}-й раз")
        elif t == "damage_dealt":
            amount = int(p.get("amount") or 0)
            self.damage_dealt += amount
            b.damage_dealt += amount
        elif t == "damage_received":
            amount = int(p.get("amount") or 0)
            self.damage_received += amount
            b.damage_received += amount
        elif t == "damage_record":
            damage = int(p.get("damage") or 0)
            if damage > self.damage_record:
                facts.append(
                    f"новый рекорд урона за сессию: {damage} (прошлый {self.damage_record})"
                )
                self.damage_record = damage
        elif t == "battle_result":
            self.battles += 1
            if p.get("outcome") == "win":
                self.wins += 1
        elif t == "crit":
            self.crits += 1
            b.crits += 1
        elif t == "spotted":
            self.spots += 1
            b.spots += 1
        elif t == "assist":
            amount = int(p.get("amount") or 0)
            self.assist_total += amount
            self.assist_count += 1
            b.assist_total += amount
        elif t == "blocked":
            amount = int(p.get("amount") or 0)
            self.blocked_total += amount
            self.blocked_count += 1
            b.blocked_total += amount
        elif t == "fire":
            self.fires += 1
            b.fires += 1
            if b.fires >= 2:
                facts.append(f"стример горит уже {b.fires}-й раз за бой")
        elif t == "low_hp":
            self.low_hp_events += 1
            b.low_hp = True
        elif t == "damage_milestone":
            total = int(p.get("total") or 0)
            facts.append(f"урон за бой перевалил за {total}")
        elif t == "battle_start":
            # Новый бой — свежая боевая память, танк переносим из ангара.
            self.battle = BattleMemory(
                map_name=p.get("map"), mode=p.get("mode"), tank=self.current_tank
            )
        elif t == "vehicle_change":
            tank = str(p.get("tank") or "").strip()
            if tank:
                self.current_tank = tank
                self.battle.tank = tank
        return facts

    def battle_lines(self) -> list[str]:
        """Контекст текущего боя — основа каждой реплики."""
        return self.battle.lines()

    def session_lines(self) -> list[str]:
        """Итоги сессии — для редких подколок и команды !stats."""
        lines: list[str] = []
        if self.battles:
            lines.append(f"боёв за сессию: {self.battles}, побед: {self.wins}")
        if self.frags:
            lines.append(f"фрагов за сессию: {self.frags}")
        if self.deaths:
            lines.append(f"смертей за сессию: {self.deaths}")
            top = self.deaths_by_killer.most_common(1)
            if top and top[0][1] >= 2:
                lines.append(f"главный обидчик: «{top[0][0]}» ({top[0][1]} смертей)")
        if self.ammo_racks:
            lines.append(f"взрывов БК: {self.ammo_racks}")
        if self.damage_dealt:
            lines.append(f"нанесено урона за сессию: {self.damage_dealt}")
        if self.damage_received:
            lines.append(f"получено урона за сессию: {self.damage_received}")
        if self.damage_record:
            lines.append(f"рекорд урона: {self.damage_record}")
        if self.assist_total:
            lines.append(f"ассиста за сессию: {self.assist_total}")
        if self.blocked_total:
            lines.append(f"заблокировано бронёй за сессию: {self.blocked_total}")
        if self.fires:
            lines.append(f"пожаров за сессию: {self.fires}")
        return lines

    def summary_lines(self) -> list[str]:
        """Полная сводка (панель/статус): бой + сессия."""
        return self.battle_lines() + self.session_lines()
