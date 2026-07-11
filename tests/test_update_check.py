"""Проверка обновлений: сравнение версий и запрос к GitHub Releases."""

import httpx

from stream_director.update_check import apply_update_status, fetch_update, is_newer


def test_is_newer_basic():
    assert is_newer("0.2.0", "0.1.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.0.9", "0.1.0")


def test_is_newer_v_prefix():
    assert is_newer("v0.2.0", "0.1.0")
    assert not is_newer("v0.1.0", "0.1.0")


def test_is_newer_garbage_is_false():
    # Кривой тег с GitHub не должен ронять приложение — просто «не новее».
    assert not is_newer("beta", "0.1.0")
    assert not is_newer("", "0.1.0")
    assert not is_newer("1.2.x", "0.1.0")


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_fetch_update_newer_version():
    def handler(request):
        assert "api.github.com" in str(request.url)
        return httpx.Response(
            200, json={"tag_name": "v9.9.9", "html_url": "https://example.com/rel"}
        )

    info = await fetch_update("0.1.0", transport=_transport(handler))
    assert info == {"version": "9.9.9", "url": "https://example.com/rel"}


async def test_fetch_update_same_version_returns_none():
    def handler(request):
        return httpx.Response(200, json={"tag_name": "v0.1.0", "html_url": "x"})

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_fetch_update_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom")

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_fetch_update_http_error_returns_none():
    def handler(request):
        return httpx.Response(403, json={"message": "rate limit"})

    assert await fetch_update("0.1.0", transport=_transport(handler)) is None


async def test_apply_update_status_sets_key():
    def handler(request):
        return httpx.Response(200, json={"tag_name": "v9.9.9", "html_url": "u"})

    statuses: dict = {}
    await apply_update_status(statuses, "0.1.0", transport=_transport(handler))
    assert statuses["update_available"] == {"version": "9.9.9", "url": "u"}


async def test_apply_update_status_no_update_no_key():
    def handler(request):
        raise httpx.ConnectError("offline")

    statuses: dict = {}
    await apply_update_status(statuses, "0.1.0", transport=_transport(handler))
    assert "update_available" not in statuses


async def test_fetch_update_follows_rename_redirect():
    # Переименование репозитория: GitHub API отдаёт 301 на новый адрес —
    # проверка обязана дойти до него, а не молча вернуть None.
    def handler(request):
        if "old-name" in str(request.url):
            return httpx.Response(
                301, headers={"Location": "https://api.github.com/repos/o/new/releases/latest"}
            )
        return httpx.Response(200, json={"tag_name": "v9.9.9", "html_url": "u"})

    info = await fetch_update(
        "0.1.0",
        url="https://api.github.com/repos/o/old-name/releases/latest",
        transport=_transport(handler),
    )
    assert info == {"version": "9.9.9", "url": "u"}
