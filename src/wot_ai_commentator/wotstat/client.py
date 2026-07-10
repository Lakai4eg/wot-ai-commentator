"""Универсальный asyncio-клиент протокола WotStat DataProvider.

Мод wotstat-data-provider поднимает в игре простой WebSocket-сервер на
`ws://localhost:38200` (без авторизации, plain-JSON текстовые фреймы) и шлёт
ровно три типа сообщений, дискриминатор — поле `type`:

- `init`    — снапшот всех состояний сразу после коннекта:
              `{"type":"init","states":[{"path","value"}, ...]}`;
- `state`   — изменение одного состояния: `{"type":"state","path","value"}`;
- `trigger` — событие без хранения значения: `{"type":"trigger","path","value"}`
              (`value` может отсутствовать / быть null).

Клиент держит плоское дерево состояний (path строкой через точку → value),
раздаёт подписки на смену состояний и на триггеры и живёт всю сессию,
переподключаясь с бэкоффом (игра может быть выключена часами).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Callable

import websockets

log = logging.getLogger(__name__)

# Часовой «значение отсутствует» — чтобы отличить «нет пути» от «значение None».
_MISSING = object()

StateCb = Callable[[Any, Any], None]  # cb(new, old)
TriggerCb = Callable[[Any], None]  # cb(value)


class DataProviderClient:
    """Клиент WotStat: дерево состояния, подписки, реконнект-луп."""

    def __init__(self, url: str = "ws://localhost:38200") -> None:
        self.url = url
        # статус только "connected" (после первого init) | "waiting"
        self.status: str = "waiting"
        # time.time() последнего полученного сообщения (любого типа)
        self.last_event_at: float | None = None
        self._state: dict[str, Any] = {}
        self._subs: dict[str, list[StateCb]] = defaultdict(list)
        self._triggers: dict[str, list[TriggerCb]] = defaultdict(list)
        self._running = False
        self._ws: Any = None  # текущее соединение — для stop()

    # --- публичный доступ к состоянию и подпискам ---

    def get(self, path: str, default: Any = None) -> Any:
        """Текущее значение состояния по пути или default."""
        return self._state.get(path, default)

    def subscribe(self, path: str, cb: StateCb) -> None:
        """Подписка на смену состояния: cb(new, old) при изменении значения."""
        self._subs[path].append(cb)

    def on_trigger(self, path: str, cb: TriggerCb) -> None:
        """Подписка на триггер (событие): cb(value) при каждом срабатывании."""
        self._triggers[path].append(cb)

    # --- обработка входящих сообщений (public для тестов) ---

    async def handle_message(self, raw: str) -> None:
        """Разобрать одно сообщение протокола и разослать подписчикам.

        Битый JSON или неизвестный тип не роняют клиента — только лог.
        """
        self.last_event_at = time.time()
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            log.debug("WotStat: не JSON-сообщение отброшено: %.120r", raw)
            return
        if not isinstance(msg, dict):
            log.debug("WotStat: сообщение не объект отброшено: %.120r", raw)
            return

        mtype = msg.get("type")
        if mtype == "init":
            self._apply_init(msg.get("states") or [])
            # статус "connected" наступает именно после init, а не после
            # открытия сокета (как в официальном SDK).
            self.status = "connected"
        elif mtype == "state":
            self._apply_state(msg.get("path"), msg.get("value"))
        elif mtype == "trigger":
            self._dispatch_trigger(msg.get("path"), msg.get("value"))
        else:
            log.debug("WotStat: неизвестный тип сообщения %r", mtype)

    def _apply_init(self, states: list) -> None:
        """Снапшот всех состояний перезаписывает дерево целиком.

        Подписчики путей вызываются, если новое значение отличается от
        сохранённого (при первом коннекте old = None).
        """
        new_tree: dict[str, Any] = {}
        for item in states:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str):
                continue
            new_tree[path] = item.get("value")

        old_tree = self._state
        self._state = new_tree
        for path, value in new_tree.items():
            old = old_tree.get(path, _MISSING)
            if old is _MISSING or old != value:
                self._notify_state(path, value, None if old is _MISSING else old)

    def _apply_state(self, path: Any, value: Any) -> None:
        """Изменение одного состояния; подписчики — только при реальной смене."""
        if not isinstance(path, str):
            return
        old = self._state.get(path, _MISSING)
        if old is not _MISSING and old == value:
            return
        self._state[path] = value
        self._notify_state(path, value, None if old is _MISSING else old)

    def _notify_state(self, path: str, new: Any, old: Any) -> None:
        for cb in list(self._subs.get(path, ())):
            try:
                cb(new, old)
            except Exception:
                log.exception("WotStat: подписчик state %s упал", path)

    def _dispatch_trigger(self, path: Any, value: Any) -> None:
        if not isinstance(path, str):
            return
        for cb in list(self._triggers.get(path, ())):
            try:
                cb(value)
            except Exception:
                log.exception("WotStat: обработчик trigger %s упал", path)

    # --- реконнект-луп ---

    async def run(self) -> None:
        """Жить всю сессию: подключаться и переподключаться с бэкоффом 1→5 с.

        Игра может быть выключена часами, поэтому лог «жду игру» пишется один
        раз на серию неудач, а не на каждую попытку.
        """
        self._running = True
        backoff = 1.0
        waiting_logged = False
        while self._running:
            try:
                async with websockets.connect(self.url, open_timeout=5) as ws:
                    self._ws = ws
                    backoff = 1.0
                    waiting_logged = False
                    log.info("WotStat: подключено к %s", self.url)
                    async for raw in ws:
                        await self.handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not waiting_logged:
                    log.info("WotStat: сервер недоступен (%s), жду игру…", e)
                    waiting_logged = True
            finally:
                self._ws = None
                self.status = "waiting"
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

    def stop(self) -> None:
        """Остановить реконнект-луп и закрыть текущее соединение."""
        self._running = False
        ws = self._ws
        if ws is not None:
            try:
                asyncio.get_running_loop().create_task(ws.close())
            except RuntimeError:
                pass  # нет активного лупа — луп сам выйдет по флагу
