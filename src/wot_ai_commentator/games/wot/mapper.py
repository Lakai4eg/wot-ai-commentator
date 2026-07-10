"""EventMapper: игровые пути/триггеры WotStat → Stimulus.

Маппер не знает про транспорт — принимает duck-typed клиент с интерфейсом из
спеки §3 (`get(path, default)`, `subscribe(path, cb)` c `cb(new, old)`,
`on_trigger(path, cb)` c `cb(value)`); в тестах это FakeClient. На каждое
интересное событие боя маппер собирает `Stimulus` и отдаёт его в `submit`.

Тихие события (§4.2) несут `payload["silent"] = True`: директор регистрирует их
в памяти сессии, но реплику не генерирует.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Callable

from ...events import Priority, Stimulus

log = logging.getLogger(__name__)

# Порог «крупного» урона: ниже — реплика с низким приоритетом.
_BIG_DAMAGE = 150
# Пожар тикает каждые пару секунд — не частим репликами про огонь.
_FIRE_DEDUP_S = 15.0
# Засвет срабатывает на каждый обнаруженный танк — редкая реплика вместо спама.
_SPOTTED_DEDUP_S = 45.0
# Ассист/блок копятся мелкими порциями — реагируем на заметный рост.
_ACCUM_STEP = 200
# Доля ХП, ниже которой считаем «на грани».
_LOW_HP_FRACTION = 0.2


class EventMapper:
    """Переводит поток состояний и триггеров WotStat в игровые стимулы."""

    def __init__(self, client: Any, submit: Callable[[Stimulus], None]) -> None:
        self.client = client
        self.submit = submit

        # Диагностика для /api/status.
        self._events_found = 0
        self._last_events: deque[str] = deque(maxlen=12)

        # Пер-боевые трекеры (сбрасываются на battle_start).
        self._reset_battle_trackers()

        # Триггеры (события без хранимого значения).
        client.on_trigger("battle.onDamage", self._on_damage)
        client.on_trigger("battle.onPlayerFeedback", self._on_feedback)
        client.on_trigger("battle.onBattleResult", self._on_battle_result)

        # Состояния (реагируем на смену значения).
        client.subscribe("battle.isAlive", self._on_alive_change)
        client.subscribe("battle.efficiency.assist", self._on_assist)
        client.subscribe("battle.efficiency.blocked", self._on_blocked)
        client.subscribe("battle.efficiency.damage", self._on_damage_total)
        client.subscribe("battle.teamBases", self._on_team_bases)
        client.subscribe("battle.health", self._on_health)
        client.subscribe("game.state", self._on_game_state)
        client.subscribe("hangar.vehicle.info", self._on_vehicle_change)

    # --- диагностика ---------------------------------------------------

    @property
    def diag(self) -> dict:
        return {
            "game_state": self.client.get("game.state"),
            "events_found": self._events_found,
            "last_events": self._last_events,
        }

    # --- пер-боевые трекеры --------------------------------------------

    def _reset_battle_trackers(self) -> None:
        self._last_attacker: str | None = None  # для события смерти
        self._assist_emitted = 0  # значение накопителя на момент последней реплики
        self._blocked_emitted = 0
        self._damage_milestone = 0  # достигнутая веха урона (в тысячах)
        self._low_hp_flagged = False
        self._last_fire_at = 0.0
        self._last_spotted_at = 0.0
        self._base_points: dict[Any, int] = {}  # baseID → последние points
        self._base_emitted: set[Any] = set()  # baseID, по которым уже дали реплику

    # --- вспомогательное -----------------------------------------------

    def _is_me(self, vehicle: Any) -> bool:
        """Танк принадлежит стримеру (сравнение по playerId с player.id)."""
        if not isinstance(vehicle, dict):
            return False
        my_id = self.client.get("player.id")
        return my_id is not None and vehicle.get("playerId") == my_id

    @staticmethod
    def _is_spg(vehicle: Any) -> bool:
        """Атакующий — арта (САУ): класс SPG в classTag/class/tag."""
        if not isinstance(vehicle, dict):
            return False
        marker = " ".join(
            str(vehicle.get(k) or "") for k in ("class", "classTag", "type", "tag")
        )
        return "SPG" in marker and "AT-SPG" not in marker

    @staticmethod
    def _vehicle_name(vehicle: Any) -> str:
        if not isinstance(vehicle, dict):
            return "неизвестный"
        return (
            vehicle.get("localizedShortName")
            or vehicle.get("localizedName")
            or vehicle.get("playerName")
            or vehicle.get("tag")
            or "неизвестный"
        )

    @staticmethod
    def _vehicle_level(vehicle: Any) -> int | None:
        """Уровень (тир) танка из info — int или None, если поля нет."""
        if not isinstance(vehicle, dict):
            return None
        for key in ("level", "tier"):
            value = vehicle.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _emit(
        self,
        type_: str,
        payload: dict,
        priority: Priority = Priority.NORMAL,
        ttl_s: float = 20.0,
    ) -> None:
        stim = Stimulus(
            kind="game_event",
            type=type_,
            game="wot",
            priority=priority,
            payload=payload,
            ttl_s=ttl_s,
        )
        self._events_found += 1
        self._last_events.append(type_)
        try:
            self.submit(stim)
        except Exception:  # шоу продолжается даже если приёмник упал
            log.exception("EventMapper: submit упал на событии %s", type_)

    # --- триггеры ------------------------------------------------------

    def _on_damage(self, value: Any) -> None:
        """battle.onDamage: урон по ЛЮБОМУ видимому танку — фильтруем свой."""
        if not isinstance(value, dict):
            return
        attacker = value.get("attacker")
        target = value.get("target")
        damage = int(value.get("damage") or 0)
        reason = value.get("reason") or ""

        if self._is_me(target):
            # Запоминаем обидчика — пригодится для события смерти.
            if isinstance(attacker, dict):
                self._last_attacker = self._vehicle_name(attacker)
            if reason == "fire":
                # Пожар — отдельное событие с дедупом (тикает часто).
                self._maybe_fire()
                return
            if damage <= 0:
                return
            prio = Priority.LOW if damage < _BIG_DAMAGE else Priority.NORMAL
            payload = {
                "amount": damage,
                "source": self._vehicle_name(attacker),
                "reason": reason,
            }
            if self._is_spg(attacker):
                # Прилёт от арты — отдельный повод для ехидства.
                payload["from_arta"] = True
                prio = Priority.NORMAL
            self._emit("damage_received", payload, prio, ttl_s=10)
            return

        if self._is_me(attacker):
            if damage <= 0:
                return
            prio = Priority.LOW if damage < _BIG_DAMAGE else Priority.NORMAL
            self._emit(
                "damage_dealt",
                {"amount": damage, "target": self._vehicle_name(target), "reason": reason},
                prio,
                ttl_s=10,
            )
            return
        # Ни я ударил, ни по мне — чужой урон, игнорируем.

    def _maybe_fire(self) -> None:
        now = time.time()
        if now - self._last_fire_at < _FIRE_DEDUP_S:
            return
        self._last_fire_at = now
        self._emit("fire", {}, Priority.HIGH, ttl_s=15)

    def _on_feedback(self, value: Any) -> None:
        """battle.onPlayerFeedback: фраг / крит / засвет по типу события."""
        if not isinstance(value, dict):
            return
        ftype = value.get("type")
        data = value.get("data")
        if not isinstance(data, dict):
            data = {}

        if ftype == "kill":
            self._emit(
                "frag",
                {"target": self._vehicle_name(data.get("vehicle"))},
                Priority.HIGH,
                ttl_s=20,
            )
        elif ftype == "crit":
            self._emit("crit", {"detail": data.get("critsCount")}, Priority.LOW, ttl_s=10)
        elif ftype == "spotted":
            # Засвет частит — реплику даём не чаще раза в _SPOTTED_DEDUP_S.
            now = time.time()
            if now - self._last_spotted_at < _SPOTTED_DEDUP_S:
                return
            self._last_spotted_at = now
            self._emit("spotted", {}, Priority.LOW, ttl_s=8)

    def _on_battle_result(self, value: Any) -> None:
        """battle.onBattleResult: тихое событие итога боя (только память)."""
        outcome = "unknown"
        if isinstance(value, dict):
            common = value.get("common")
            winner = common.get("winnerTeam") if isinstance(common, dict) else None
            my_team = self.client.get("battle.arena.team")
            if winner is None:
                outcome = "unknown"
            elif winner == 0:
                outcome = "draw"
            elif my_team is not None and winner == my_team:
                outcome = "win"
            else:
                outcome = "loss"
        self._emit("battle_result", {"outcome": outcome, "silent": True}, Priority.NORMAL, ttl_s=20)

    # --- состояния -----------------------------------------------------

    def _on_alive_change(self, new: Any, old: Any) -> None:
        """battle.isAlive true→false — смерть; убийца = последний атаковавший."""
        if old and not new:
            self._emit(
                "death",
                {"killer": self._last_attacker or "неизвестный"},
                Priority.HIGH,
                ttl_s=30,
            )

    def _on_assist(self, new: Any, old: Any) -> None:
        value = int(new or 0)
        if value - self._assist_emitted >= _ACCUM_STEP:
            amount = value - self._assist_emitted
            self._assist_emitted = value
            self._emit("assist", {"amount": amount}, Priority.LOW, ttl_s=8)

    def _on_blocked(self, new: Any, old: Any) -> None:
        value = int(new or 0)
        if value - self._blocked_emitted >= _ACCUM_STEP:
            amount = value - self._blocked_emitted
            self._blocked_emitted = value
            self._emit("blocked", {"amount": amount}, Priority.LOW, ttl_s=8)

    def _on_damage_total(self, new: Any, old: Any) -> None:
        """battle.efficiency.damage пересёк очередную тысячу — веха урона."""
        value = int(new or 0)
        milestone = value // 1000
        if milestone > self._damage_milestone:
            self._damage_milestone = milestone
            self._emit("damage_milestone", {"total": value}, Priority.NORMAL, ttl_s=20)

    def _on_team_bases(self, new: Any, old: Any) -> None:
        """battle.teamBases: рост points на базе = идёт захват.

        Ключ словаря — команда-владелец базы (строкой). Растут очки на нашей
        базе → захватывает враг (theirs); на чужой → захватываем мы (ours).
        Дедуп до сброса points в 0.
        """
        if not isinstance(new, dict):
            return
        my_team = self.client.get("battle.arena.team")
        for team_key, bases in new.items():
            if not isinstance(bases, list):
                continue
            try:
                owner_team = int(team_key)
            except (TypeError, ValueError):
                owner_team = None
            for base in bases:
                if not isinstance(base, dict):
                    continue
                base_id = base.get("baseID")
                points = int(base.get("points") or 0)
                prev = self._base_points.get(base_id, 0)
                self._base_points[base_id] = points
                if points <= 0:
                    self._base_emitted.discard(base_id)
                    continue
                if points > prev and base_id not in self._base_emitted:
                    self._base_emitted.add(base_id)
                    side = "theirs" if owner_team == my_team else "ours"
                    self._emit("base_capture", {"side": side}, Priority.HIGH, ttl_s=20)

    def _on_health(self, new: Any, old: Any) -> None:
        """battle.health < 20% max — тихая пометка «на грани» (раз за бой)."""
        health = int(new or 0)
        max_hp = int(self.client.get("battle.maxHealth") or 0)
        if max_hp <= 0 or health <= 0:
            return
        if health < _LOW_HP_FRACTION * max_hp and not self._low_hp_flagged:
            self._low_hp_flagged = True
            self._emit("low_hp", {"silent": True}, Priority.NORMAL, ttl_s=8)

    def _on_game_state(self, new: Any, old: Any) -> None:
        """game.state → "battle": начало боя, сброс пер-боевых трекеров."""
        if new == "battle":
            self._reset_battle_trackers()
            self._emit(
                "battle_start",
                {
                    "map": self.client.get("battle.arena.localizedName"),
                    "mode": self.client.get("battle.arena.mode"),
                    "silent": True,
                },
                Priority.NORMAL,
                ttl_s=20,
            )

    def _on_vehicle_change(self, new: Any, old: Any) -> None:
        """hangar.vehicle.info: смена / выбор танка — обновляем текущий танк."""
        if not new:
            return
        tank = self._vehicle_name(new) if isinstance(new, dict) else str(new)
        self._emit("vehicle_change", {"tank": tank, "silent": True}, Priority.NORMAL, ttl_s=20)
        # «Всеми любимые» танки 11 уровня — отдельный повод для подколки.
        if self._vehicle_level(new) == 11:
            self._emit("tier11", {"tank": tank}, Priority.NORMAL, ttl_s=20)
