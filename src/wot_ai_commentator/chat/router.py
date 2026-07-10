"""Роутер чат-команд: белый список, роли, кулдауны → стимулы директору."""

from __future__ import annotations

import logging
import time

from ..config import Settings
from ..db import WhitelistDB
from ..director import Director
from ..events import Priority, Stimulus
from .commands import ADMIN_COMMANDS, parse_command, parse_mute_arg

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
        if role is None:
            return  # не из белого списка — молча игнорируем
        if cmd.name in ADMIN_COMMANDS and role != "admin":
            log.info("У %s нет прав на !%s", username, cmd.name)
            return

        now = time.time()
        last = self._last_command_at.get(username, 0.0)
        if now - last < self.settings.user_cooldown_s:
            return
        self._last_command_at[username] = now

        if cmd.name == "mute":
            seconds = parse_mute_arg(cmd.arg) or 300.0
            self.director.submit(
                Stimulus(kind="control", type="mute", payload={"seconds": seconds})
            )
        elif cmd.name == "dir":
            self.director.submit(
                Stimulus(
                    kind="chat_order",
                    type="dir",
                    priority=Priority.NORMAL,
                    payload={"text": cmd.arg, "username": username},
                    ttl_s=60.0,
                )
            )
        else:  # roast / hype / stats
            self.director.submit(
                Stimulus(
                    kind="chat_order",
                    type=cmd.name,
                    priority=Priority.NORMAL,
                    payload={"username": username},
                    ttl_s=60.0,
                )
            )
