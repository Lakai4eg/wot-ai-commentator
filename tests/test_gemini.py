
import httpx
import pytest

from wot_ai_commentator.commentary.gemini import GeminiBackend


def make_backend(handler):
    backend = GeminiBackend(api_key="test-key", model="gemini-3-flash", timeout_s=1.0)
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return backend


@pytest.mark.asyncio
async def test_success_returns_text():
    def handler(request):
        assert "gemini-3-flash:generateContent" in str(request.url)
        assert request.headers["x-goog-api-key"] == "test-key"
        body = {
            "candidates": [
                {"content": {"parts": [{"text": "  Отличный выстрел!  "}]}}
            ]
        }
        return httpx.Response(200, json=body)

    backend = make_backend(handler)
    assert await backend.generate("prompt") == "Отличный выстрел!"
    assert backend.last_error is None


@pytest.mark.asyncio
async def test_429_returns_none_with_error():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "quota"}})

    backend = make_backend(handler)
    assert await backend.generate("prompt") is None
    assert "429" in backend.last_error


@pytest.mark.asyncio
async def test_timeout_returns_none():
    def handler(request):
        raise httpx.ConnectTimeout("boom")

    backend = make_backend(handler)
    assert await backend.generate("prompt") is None
    assert backend.last_error


@pytest.mark.asyncio
async def test_malformed_json_returns_none():
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    backend = make_backend(handler)
    assert await backend.generate("prompt") is None


@pytest.mark.asyncio
async def test_no_api_key_returns_none_without_request():
    backend = GeminiBackend(api_key="", model="m", timeout_s=1.0)
    assert await backend.generate("prompt") is None
    assert backend.last_error
