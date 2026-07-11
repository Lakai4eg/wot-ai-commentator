"""Анонимный читатель чата Twitch (IRC, justinfan — без OAuth)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
PORT = 6667

# :nick!nick@nick.tmi.twitch.tv PRIVMSG #channel :message text
_PRIVMSG_RE = re.compile(r"^:(\w+)!\S+\s+PRIVMSG\s+#\S+\s+:(.*)$")

OnMessage = Callable[[str, str], Awaitable[None]]


def parse_privmsg(line: str) -> tuple[str, str] | None:
    m = _PRIVMSG_RE.match(line.strip())
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


class TwitchChatReader:
    def __init__(self, channel: str, on_message: OnMessage):
        self.channel = channel.strip().lstrip("#").lower()
        self.on_message = on_message
        self.status = "disconnected"
        self._running = False
        self._writer: asyncio.StreamWriter | None = None
        self._wake = asyncio.Event()

    def set_channel(self, channel: str) -> None:
        """Горячая смена канала: рвём соединение, run() переподключится сразу."""
        new = channel.strip().lstrip("#").lower()
        if new == self.channel:
            return
        self.channel = new
        self._wake.set()
        if self._writer is not None:
            self._writer.close()

    async def run(self) -> None:
        self._running = True
        backoff = 2.0
        while self._running:
            if not self.channel:
                self.status = "disconnected"
                log.info("Канал Twitch не задан — жду настройки из панели")
                await self._wake.wait()
                self._wake.clear()
                continue
            try:
                self.status = "connecting"
                await self._session()
                backoff = 2.0
            except (OSError, asyncio.IncompleteReadError, ConnectionError) as e:
                log.warning("Чат Twitch отвалился (%s), реконнект через %.0f с", e, backoff)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Неожиданная ошибка чата, реконнект через %.0f с", backoff)
            self.status = "disconnected"
            if self._running:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=backoff)
                    backoff = 2.0  # разбудила смена канала — подключаемся сразу
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 60.0)
                self._wake.clear()

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        self._writer = writer
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
            await writer.drain()
            self.status = "connected"
            log.info("Чат Twitch подключён: #%s", self.channel)
            while self._running:
                raw = await reader.readline()
                if not raw:
                    raise ConnectionError("соединение закрыто сервером")
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("PING"):
                    writer.write(f"PONG{line[4:]}\r\n".encode())
                    await writer.drain()
                    continue
                parsed = parse_privmsg(line)
                if parsed:
                    try:
                        await self.on_message(*parsed)
                    except Exception:
                        log.exception("Обработчик сообщения чата упал")
        finally:
            self._writer = None
            writer.close()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
