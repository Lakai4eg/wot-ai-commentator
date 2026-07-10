import httpx
import pytest

from stream_director.commentary.gemini import GeminiBackend
from stream_director.commentary.openai_compat import OpenAICompatBackend
from stream_director.commentary.switch import SwitchBackend
from stream_director.config import Settings


def make_backend(handler, base_url="https://api.groq.com/openai/v1", api_key="test-key"):
    backend = OpenAICompatBackend(
        base_url=base_url, api_key=api_key, model="llama-3.3-70b", timeout_s=1.0
    )
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return backend


@pytest.mark.asyncio
async def test_success_returns_text():
    def handler(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  Красиво зашло!  "}}]},
        )

    backend = make_backend(handler)
    assert await backend.generate("prompt") == "Красиво зашло!"
    assert backend.last_error is None


@pytest.mark.asyncio
async def test_no_auth_header_without_key():
    """Ollama работает без ключа — Authorization не отправляется."""
    def handler(request):
        assert "authorization" not in {k.lower() for k in request.headers}
        return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

    backend = make_backend(handler, base_url="http://localhost:11434/v1", api_key="")
    assert await backend.generate("prompt") == "ок"


@pytest.mark.asyncio
async def test_429_returns_none_with_error():
    def handler(request):
        return httpx.Response(429, json={"error": "rate limit"})

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
async def test_unconfigured_returns_none_without_request():
    backend = OpenAICompatBackend(base_url="", model="")
    assert not backend.configured
    assert await backend.generate("prompt") is None
    assert backend.last_error


@pytest.mark.asyncio
async def test_switch_backend_routes_by_provider():
    settings = Settings(gemini_api_key="g-key")

    def gemini_handler(request):
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "от gemini"}]}}]}
        )

    def openai_handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "от openai"}}]})

    gemini = GeminiBackend(api_key="g-key", model="m", timeout_s=1.0)
    gemini._client = httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler))
    openai = make_backend(openai_handler)
    switch = SwitchBackend(settings, gemini, openai)

    settings.llm_provider = "gemini"
    assert await switch.generate("p") == "от gemini"
    assert switch.configured

    settings.llm_provider = "openai"
    assert await switch.generate("p") == "от openai"
    assert switch.configured


def test_switch_configured_reflects_provider():
    settings = Settings(gemini_api_key="", llm_provider="gemini")
    switch = SwitchBackend(
        settings,
        GeminiBackend(api_key="", model="m"),
        OpenAICompatBackend(base_url="http://x", api_key="", model="m"),
    )
    assert not switch.configured  # gemini без ключа
    settings.llm_provider = "openai"
    assert switch.configured  # openai: base_url + model достаточно
