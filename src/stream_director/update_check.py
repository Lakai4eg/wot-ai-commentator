"""Проверка обновлений: GitHub Releases → баннер «доступна версия» в панели.

Любая ошибка (нет сети, rate limit, кривой JSON) молча гасится — проверка
обновлений не имеет права мешать работе приложения.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

GITHUB_LATEST_URL = (
    "https://api.github.com/repos/Lakai4eg/wot-ai-commentator/releases/latest"
)


def is_newer(latest: str, current: str) -> bool:
    """Числовое сравнение версий вида X.Y.Z (допустим префикс v).

    Непарсибельные строки — False: кривой тег не повод для баннера.
    """

    def parse(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.strip().lstrip("v").split("."))

    try:
        return parse(latest) > parse(current)
    except ValueError:
        return False


async def fetch_update(
    current: str,
    url: str = GITHUB_LATEST_URL,
    transport: httpx.BaseTransport | None = None,
) -> dict | None:
    """Свежайший релиз новее current → {"version", "url"}, иначе None."""
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            data = r.json()
        tag = str(data.get("tag_name", ""))
        if is_newer(tag, current):
            return {"version": tag.lstrip("v"), "url": str(data.get("html_url", ""))}
    except Exception:
        log.debug("проверка обновлений не удалась", exc_info=True)
    return None


async def apply_update_status(
    statuses: dict,
    current: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Одноразовая фоновая проверка: нашли новее — кладём в статусы панели."""
    info = await fetch_update(current, transport=transport)
    if info is not None:
        statuses["update_available"] = info
