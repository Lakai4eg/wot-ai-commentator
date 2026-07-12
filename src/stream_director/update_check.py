"""Проверка обновлений: GitHub Releases → баннер «доступна версия» в панели.

Любая ошибка (нет сети, rate limit, кривой JSON) молча гасится — проверка
обновлений не имеет права мешать работе приложения.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

GITHUB_LATEST_URL = (
    "https://api.github.com/repos/Lakai4eg/game-ai-commentator/releases/latest"
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
    """Свежайший релиз новее current → словарь, иначе None.

    Возвращает {"version", "url", "zip_url", "sha_url"}: первые два поля нужны
    баннеру в панели, вторые два — апдейтеру. Ассетов с ожидаемыми именами в
    релизе может не быть (собрали руками, выложили не всё) — тогда ссылки
    пустые, и апдейтер просто не предложит обновление.
    """
    try:
        # follow_redirects: при переименовании репозитория GitHub API отвечает
        # 301, а httpx по умолчанию редиректы не ходит — проверка молча умирала бы.
        async with httpx.AsyncClient(
            timeout=5.0, transport=transport, follow_redirects=True
        ) as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            data = r.json()
        tag = str(data.get("tag_name", ""))
        if not is_newer(tag, current):
            return None
        version = tag.lstrip("v")
        assets = {
            str(a.get("name", "")): str(a.get("browser_download_url", ""))
            for a in data.get("assets", [])
        }
        zip_name = f"StreamDirector-v{version}-win64.zip"
        return {
            "version": version,
            "url": str(data.get("html_url", "")),
            "zip_url": assets.get(zip_name, ""),
            "sha_url": assets.get(f"{zip_name}.sha256", ""),
        }
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
