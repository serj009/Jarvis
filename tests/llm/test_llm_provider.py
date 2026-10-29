"""Tests for jarvis.llm.llm_provider — APILLMClient adapter.

All external calls are fully mocked; no real APIs needed. Tests cover:
  - APILLMClient.from_config factory for each provider
  - stream_chat interface compatibility with OllamaClient
  - vision_chat delegation to _VisionAPIProvider
  - Error handling: connection, auth, generic API errors
  - load/unload/warm lifecycle (no-ops for API)
  - System prompt prepending in _prepare_messages
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.llm.llm_provider import APILLMClient, _build_llm_provider, _VisionAPIProvider
from jarvis.llm.model_provider import (
    AnthropicProvider,
    GroqProvider,
    OpenAIProvider,
    ProviderAuthError,
    ProviderConnectionError,
)
from jarvis.llm.model_provider import (
    ChatChunk as ProviderChunk,
)
from jarvis.llm.ollama_client import ChatChunk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_llm_config(**overrides):
    """Build a mock LLMConfig."""
    defaults = {
        "provider": "openai",
        "model": "qwen3:8b",
        "api_model": "gpt-4o",
        "api_key": "sk-test",
        "api_base_url": "",
        "temperature": 0.7,
        "max_tokens": 1024,
        "system_prompt": "You are Jarvis.",
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_vision_config(**overrides):
    """Build a mock VisionConfig."""
    defaults = {
        "provider": "ollama",
        "model": "llava:7b",
        "api_model": "",
        "api_key": "",
        "api_base_url": "",
        "max_image_dim": 1280,
        "max_tokens": 512,
        "temperature": 0.2,
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_openai_provider(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert isinstance(client, APILLMClient)
        assert client.model == "gpt-4o"

    def test_anthropic_provider(self):
        cfg = _make_llm_config(
            provider="anthropic", api_model="claude-3.5-sonnet", api_key="ant-test",
        )
        client = APILLMClient.from_config(cfg)
        assert isinstance(client, APILLMClient)
        assert client.model == "claude-3.5-sonnet"

    def test_groq_provider(self):
        cfg = _make_llm_config(
            provider="groq", api_model="llama-3.3-70b-versatile", api_key="gsk-test",
        )
        client = APILLMClient.from_config(cfg)
        assert isinstance(client, APILLMClient)

    def test_ollama_provider_raises(self):
        cfg = _make_llm_config(provider="ollama")
        with pytest.raises(ValueError, match="ollama"):
            APILLMClient.from_config(cfg)

    def test_no_api_key_raises(self):
        cfg = _make_llm_config(provider="openai", api_key="")
        with pytest.raises(ProviderAuthError):
            APILLMClient.from_config(cfg)

    def test_vision_api_provider_created(self):
        llm_cfg = _make_llm_config(provider="openai", api_key="sk-test")
        vision_cfg = _make_vision_config(
            provider="openai", api_model="gpt-4o", api_key="sk-vis",
        )
        client = APILLMClient.from_config(llm_cfg, vision_cfg)
        assert client._vision is not None

    def test_vision_ollama_no_api_provider(self):
        llm_cfg = _make_llm_config(provider="openai", api_key="sk-test")
        vision_cfg = _make_vision_config(provider="ollama")
        client = APILLMClient.from_config(llm_cfg, vision_cfg)
        assert client._vision is None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_load_sets_is_loaded(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert not client.is_loaded
        await client.load()
        assert client.is_loaded

    @pytest.mark.asyncio
    async def test_warm_sets_is_loaded(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        await client.warm()
        assert client.is_loaded

    @pytest.mark.asyncio
    async def test_unload_clears_is_loaded(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        await client.load()
        await client.unload()
        assert not client.is_loaded


# ---------------------------------------------------------------------------
# stream_chat
# ---------------------------------------------------------------------------


class TestStreamChat:
    @pytest.mark.asyncio
    async def test_yields_chat_chunks(self):
        """stream_chat should yield ChatChunk objects (Ollama-compatible)."""
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)

        # Mock the underlying provider's stream_chat
        async def fake_stream(msgs, **kw):
            yield ProviderChunk(content="Hello", done=False)
            yield ProviderChunk(content=" World", done=False)
            yield ProviderChunk(content="", done=True)

        client._provider.stream_chat = fake_stream

        chunks = []
        async for chunk in client.stream_chat([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert isinstance(chunks[0], ChatChunk)
        assert chunks[0].content == "Hello"
        assert chunks[-1].done is True

    @pytest.mark.asyncio
    async def test_connection_error_yields_message(self):
        """Connection errors should yield a user-friendly message."""
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)

        async def failing_stream(msgs, **kw):
            raise ProviderConnectionError("Cannot connect")
            yield  # make it a generator  # noqa: E501, RUF027

        client._provider.stream_chat = failing_stream

        chunks = []
        async for chunk in client.stream_chat([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "API" in chunks[0].content
        assert chunks[0].done is True

    @pytest.mark.asyncio
    async def test_auth_error_yields_message(self):
        """Auth errors should yield a user-friendly message."""
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)

        async def failing_stream(msgs, **kw):
            raise ProviderAuthError("Bad key")
            yield  # noqa: E501, RUF027

        client._provider.stream_chat = failing_stream

        chunks = []
        async for chunk in client.stream_chat([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "authentication" in chunks[0].content.lower()

    @pytest.mark.asyncio
    async def test_system_prompt_prepended(self):
        """System prompt should be prepended if not in messages."""
        cfg = _make_llm_config(
            provider="openai", api_key="sk-test",
            system_prompt="You are Jarvis.",
        )
        client = APILLMClient.from_config(cfg)

        captured_msgs = []

        async def capture_stream(msgs, **kw):
            captured_msgs.extend(msgs)
            yield ProviderChunk(content="ok", done=True)

        client._provider.stream_chat = capture_stream

        async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
            pass

        assert len(captured_msgs) == 2
        assert captured_msgs[0].role == "system"
        assert captured_msgs[0].content == "You are Jarvis."

    @pytest.mark.asyncio
    async def test_no_duplicate_system_prompt(self):
        """System prompt should NOT be prepended if already in messages."""
        cfg = _make_llm_config(
            provider="openai", api_key="sk-test",
            system_prompt="You are Jarvis.",
        )
        client = APILLMClient.from_config(cfg)

        captured_msgs = []

        async def capture_stream(msgs, **kw):
            captured_msgs.extend(msgs)
            yield ProviderChunk(content="ok", done=True)

        client._provider.stream_chat = capture_stream

        async for _ in client.stream_chat([
            {"role": "system", "content": "Custom prompt"},
            {"role": "user", "content": "hi"},
        ]):
            pass

        # Should keep the existing system message, not add another
        system_msgs = [m for m in captured_msgs if m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "Custom prompt"


# ---------------------------------------------------------------------------
# vision_chat
# ---------------------------------------------------------------------------


class TestVisionChat:
    @pytest.mark.asyncio
    async def test_no_vision_provider_raises(self):
        """vision_chat should raise when no API vision provider configured."""
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert client._vision is None

        with pytest.raises(RuntimeError, match="No API vision provider"):
            await client.vision_chat(
                model="gpt-4o", prompt="describe", image_b64="abc",
            )

    @pytest.mark.asyncio
    async def test_delegates_to_vision_provider(self):
        """vision_chat should delegate to the _VisionAPIProvider."""
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)

        mock_vision = MagicMock()
        mock_vision.vision_chat = AsyncMock(return_value="A desktop with code editor")
        client._vision = mock_vision

        result = await client.vision_chat(
            model="gpt-4o", prompt="describe screen",
            image_b64="base64data", max_tokens=256, temperature=0.1,
        )

        assert result == "A desktop with code editor"
        mock_vision.vision_chat.assert_awaited_once_with(
            prompt="describe screen",
            image_b64="base64data",
            max_tokens=256,
            temperature=0.1,
        )


# ---------------------------------------------------------------------------
# _build_llm_provider
# ---------------------------------------------------------------------------


class TestBuildLlmProvider:
    def test_openai(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        p = _build_llm_provider(cfg)
        assert isinstance(p, OpenAIProvider)

    def test_anthropic(self):
        cfg = _make_llm_config(provider="anthropic", api_key="ant-test")
        p = _build_llm_provider(cfg)
        assert isinstance(p, AnthropicProvider)

    def test_groq(self):
        cfg = _make_llm_config(provider="groq", api_key="gsk-test")
        p = _build_llm_provider(cfg)
        assert isinstance(p, GroqProvider)

    def test_ollama_raises(self):
        cfg = _make_llm_config(provider="ollama")
        with pytest.raises(ValueError):
            _build_llm_provider(cfg)

    def test_unknown_raises(self):
        cfg = _make_llm_config(provider="unknown_thing")
        with pytest.raises(ValueError):
            _build_llm_provider(cfg)


# ---------------------------------------------------------------------------
# _VisionAPIProvider
# ---------------------------------------------------------------------------


class TestVisionAPIProvider:
    def test_from_config_openai(self):
        cfg = _make_vision_config(
            provider="openai", api_model="gpt-4o", api_key="sk-test",
        )
        vp = _VisionAPIProvider.from_config(cfg)
        assert vp._type == "openai"
        assert vp._model == "gpt-4o"

    def test_from_config_anthropic(self):
        cfg = _make_vision_config(
            provider="anthropic", api_model="claude-3.5-sonnet", api_key="ant-test",
        )
        vp = _VisionAPIProvider.from_config(cfg)
        assert vp._type == "anthropic"

    def test_default_model_openai(self):
        cfg = _make_vision_config(
            provider="openai", api_model="", api_key="sk-test",
        )
        vp = _VisionAPIProvider.from_config(cfg)
        assert vp._model == "gpt-4o"

    def test_default_model_anthropic(self):
        cfg = _make_vision_config(
            provider="anthropic", api_model="", api_key="ant-test",
        )
        vp = _VisionAPIProvider.from_config(cfg)
        assert "claude" in vp._model


# ---------------------------------------------------------------------------
# Interface parity with OllamaClient
# ---------------------------------------------------------------------------


class TestInterfaceParity:
    """Verify APILLMClient has the attributes IntentRouter/tools expect."""

    def test_has_stream_chat(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "stream_chat")
        assert callable(client.stream_chat)

    def test_has_vision_chat(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "vision_chat")

    def test_has_model_attribute(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "model")
        assert isinstance(client.model, str)

    def test_has_temperature_attribute(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "temperature")

    def test_has_max_tokens_attribute(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "max_tokens")

    def test_has_system_prompt_attribute(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "system_prompt")

    def test_has_is_loaded_attribute(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "is_loaded")

    def test_has_keep_alive_seconds(self):
        cfg = _make_llm_config(provider="openai", api_key="sk-test")
        client = APILLMClient.from_config(cfg)
        assert hasattr(client, "keep_alive_seconds")
