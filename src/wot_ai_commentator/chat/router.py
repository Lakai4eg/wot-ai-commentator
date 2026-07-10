"""Роутер чат-команд: белый список, кулдаун на зрителя → стимул директору."""

from __future__ import annotations

import logging
import time

from ..config import Settings
from ..db import WhitelistDB
from ..director import Director
from ..events import Priority, Stimulus
from .commands import parse_command

log = logging.getLogger(__name__)


class ChatRouter:
    def __init__(self, db: WhitelistDB, director: Director, settings: Settings):
        self.db = db
        self.director = director
        self.settings = settings
        self._last_command_at: dict[str, float] = {}

    async def handle(self, username: str, text: str) -> None:
        if not self.settings.chat_commands_enabled:
            return
        cmd = parse_command(text)
        if cmd is None:
            return
        username = username.lower()
        role = self.db.get_role(username)
        if role == "banned":
            return  # забанен — команды недоступны всегда, даже в открытом режиме
        if role is None and not self.settings.commands_open_to_all:
            return  # не из белого списка, и открытый режим выключен — игнор

        now = time.time()
        last = self._last_command_at.get(username, 0.0)
        if now - last < self.settings.user_cooldown_s:
            return
        self._last_command_at[username] = now

        # Единственная команда — !dir <текст>: заказ реплики. Приоритет HIGH,
        # чтобы заказ не стоял в очереди позади потока игровых событий.
        self.director.submit(
            Stimulus(
                kind="chat_order",
                type="dir",
                priority=Priority.HIGH,
                payload={"text": cmd.arg, "username": username},
                ttl_s=60.0,
            )
        )
