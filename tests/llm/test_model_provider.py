"""Tests for jarvis.llm.model_provider — universal LLM provider.

All external calls (httpx, OllamaClient) are fully mocked; no real
APIs or daemons needed. Tests cover:
  - from_config factory for each provider
  - Non-streaming chat() for each provider
  - Streaming stream_chat() for each provider
  - Error handling: no API key, timeout, rate limit, auth error, 404
  - GroqProvider delegation to OpenAIProvider
  - OllamaProvider model swap + restore
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from jarvis.llm.model_provider import (
    AnthropicProvider,
    ChatChunk,
    ChatMessage,
    ChatResponse,
    GroqProvider,
    ModelProvider,
    OllamaProvider,
    OpenAIProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_coding_config(**overrides: Any) -> Any:
    """Build a mock CodingConfig with the given overrides."""
    defaults = {
        "provider": "main",
        "ollama_model": "qwen2.5-coder:7b",
        "api_model": "",
        "api_key": "",
        "api_base_url": "",
        "max_iterations": 10,
        "auto_commit": False,
        "default_workspace": "",
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_ollama_client(model: str = "qwen3:8b") -> MagicMock:
    """Build a mock OllamaClient."""
    client = MagicMock()
    client.model = model
    client.temperature = 0.7
    client.max_tokens = 1024
    return client


def _make_image_gen_config(**overrides: Any) -> Any:
    """Build a mock ImageGenConfig with the given overrides."""
    defaults = {
        "image_model_provider": "main",
        "image_model_name": "",
        "image_model_api_key": "",
        "image_model_api_base_url": "",
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


SAMPLE_MESSAGES = [
    ChatMessage(role="system", content="You are a coder."),
    ChatMessage(role="user", content="Write a hello world in Python."),
]


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_provider_main_returns_ollama(self) -> None:
        cfg = _make_coding_config(provider="main")
        client = _make_ollama_client()
        provider = ModelProvider.from_config(cfg, client)
        assert isinstance(provider, OllamaProvider)

    def test_provider_main_without_client_raises(self) -> None:
        cfg = _make_coding_config(provider="main")
        with pytest.raises(ProviderError, match="OllamaClient"):
            ModelProvider.from_config(cfg, None)

    def test_provider_ollama_returns_ollama(self) -> None:
        cfg = _make_coding_config(provider="ollama")
        client = _make_ollama_client()
        provider = ModelProvider.from_config(cfg, client)
        assert isinstance(provider, OllamaProvider)

    def test_provider_ollama_without_client_raises(self) -> None:
        cfg = _make_coding_config(provider="ollama")
        with pytest.raises(ProviderError, match="OllamaClient"):
            ModelProvider.from_config(cfg, None)

    def test_provider_openai(self) -> None:
        cfg = _make_coding_config(
            provider="openai", api_model="gpt-4o", api_key="sk-test",
        )
        provider = ModelProvider.from_config(cfg)
        assert isinstance(provider, OpenAIProvider)

    def test_provider_anthropic(self) -> None:
        cfg = _make_coding_config(
            provider="anthropic", api_model="claude-3-5-sonnet-20241022",
            api_key="ant-test",
        )
        provider = ModelProvider.from_config(cfg)
        assert isinstance(provider, AnthropicProvider)

    def test_provider_groq(self) -> None:
        cfg = _make_coding_config(
            provider="groq", api_model="llama-3.3-70b-versatile",
            api_key="gsk-test",
        )
        provider = ModelProvider.from_config(cfg)
        assert isinstance(provider, GroqProvider)

    def test_unknown_provider_raises(self) -> None:
        cfg = _make_coding_config(provider="unknown_llm")
        with pytest.raises(ValueError, match="Unknown provider"):
            ModelProvider.from_config(cfg)


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    @pytest.mark.asyncio
    async def test_chat_collects_stream(self) -> None:
        """chat() should concatenate all stream chunks."""
        from jarvis.llm.ollama_client import ChatChunk as OllamaChatChunk

        client = _make_ollama_client()

        async def fake_stream(msgs, **kw):
            yield OllamaChatChunk(content="Hello", done=False)
            yield OllamaChatChunk(content=" World", done=False)
            yield OllamaChatChunk(content="", done=True)

        client.stream_chat = fake_stream

        provider = OllamaProvider(client=client, model=None)
        resp = await provider.chat(SAMPLE_MESSAGES, temperature=0.5)
        assert resp.content == "Hello World"
        assert resp.model == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_custom_model_swaps_and_restores(self) -> None:
        """When a custom model is set, stream_chat swaps it and restores."""
        from jarvis.llm.ollama_client import ChatChunk as OllamaChatChunk

        client = _make_ollama_client("qwen3:8b")
        call_model = None

        async def fake_stream(msgs, **kw):
            nonlocal call_model
            call_model = client.model
            yield OllamaChatChunk(content="ok", done=True)

        client.stream_chat = fake_stream

        provider = OllamaProvider(client=client, model="qwen2.5-coder:7b")
        await provider.chat(SAMPLE_MESSAGES)
        assert call_model == "qwen2.5-coder:7b"
        # Verify model was restored
        assert client.model == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_stream_chat_yields_chunks(self) -> None:
        from jarvis.llm.ollama_client import ChatChunk as OllamaChatChunk

        client = _make_ollama_client()

        async def fake_stream(msgs, **kw):
            yield OllamaChatChunk(content="a", done=False)
            yield OllamaChatChunk(content="b", done=True)

        client.stream_chat = fake_stream

        provider = OllamaProvider(client=client, model=None)
        chunks = []
        async for chunk in provider.stream_chat(SAMPLE_MESSAGES):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].content == "a"
        assert chunks[1].done is True

    def test_display_name_uses_client_model_when_none(self) -> None:
        client = _make_ollama_client("qwen3:8b")
        provider = OllamaProvider(client=client, model=None)
        assert "qwen3:8b" in provider.display_name()

    def test_display_name_uses_custom_model(self) -> None:
        client = _make_ollama_client("qwen3:8b")
        provider = OllamaProvider(client=client, model="codellama:7b")
        assert "codellama:7b" in provider.display_name()


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def test_no_api_key_raises(self) -> None:
        with pytest.raises(ProviderAuthError, match="api_key"):
            OpenAIProvider(model="gpt-4o", api_key="")

    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Hello from GPT"},
                    "finish_reason": "stop",
                }
            ],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        provider._client = mock_client

        resp = await provider.chat(SAMPLE_MESSAGES)
        assert resp.content == "Hello from GPT"
        assert resp.finish_reason == "stop"
        assert resp.usage["prompt_tokens"] == 10

    @pytest.mark.asyncio
    async def test_chat_connection_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        provider._client = mock_client

        with pytest.raises(ProviderConnectionError, match="cannot connect"):
            await provider.chat(SAMPLE_MESSAGES)

    @pytest.mark.asyncio
    async def test_chat_auth_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "error": {"message": "Invalid API key"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = OpenAIProvider(model="gpt-4o", api_key="sk-bad")
        provider._client = mock_client

        with pytest.raises(ProviderAuthError, match="authentication failed"):
            await provider.chat(SAMPLE_MESSAGES)

    @pytest.mark.asyncio
    async def test_chat_rate_limit(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 429
        mock_resp.json.return_value = {
            "error": {"message": "Rate limit exceeded"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        provider._client = mock_client

        with pytest.raises(ProviderRateLimitError, match="rate limit"):
            await provider.chat(SAMPLE_MESSAGES)

    @pytest.mark.asyncio
    async def test_chat_model_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 404
        mock_resp.json.return_value = {
            "error": {"message": "Model not found"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = OpenAIProvider(model="gpt-99", api_key="sk-test")
        provider._client = mock_client

        with pytest.raises(ProviderModelNotFoundError, match="not found"):
            await provider.chat(SAMPLE_MESSAGES)

    def test_display_name(self) -> None:
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        assert "gpt-4o" in provider.display_name()


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    def test_no_api_key_raises(self) -> None:
        with pytest.raises(ProviderAuthError, match="api_key"):
            AnthropicProvider(model="claude-3-5-sonnet-20241022", api_key="")

    @pytest.mark.asyncio
    async def test_chat_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(
            model="claude-3-5-sonnet-20241022", api_key="ant-test",
        )
        provider._client = mock_client

        resp = await provider.chat(SAMPLE_MESSAGES)
        assert resp.content == "Hello from Claude"
        assert resp.finish_reason == "end_turn"
        assert resp.usage["prompt_tokens"] == 12
        assert resp.usage["completion_tokens"] == 8

    @pytest.mark.asyncio
    async def test_chat_connection_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        provider = AnthropicProvider(
            model="claude-3-5-sonnet-20241022", api_key="ant-test",
        )
        provider._client = mock_client

        with pytest.raises(ProviderConnectionError, match="cannot connect"):
            await provider.chat(SAMPLE_MESSAGES)

    @pytest.mark.asyncio
    async def test_system_prompt_extracted(self) -> None:
        """Anthropic API requires system in top-level, not in messages."""
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 1},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(
            model="claude-3-5-sonnet-20241022", api_key="ant-test",
        )
        provider._client = mock_client

        await provider.chat(SAMPLE_MESSAGES)

        # Verify the body had system at top level
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["system"] == "You are a coder."
        # Messages should not contain system role
        for msg in body["messages"]:
            assert msg["role"] != "system"

    def test_display_name(self) -> None:
        provider = AnthropicProvider(
            model="claude-3-5-sonnet-20241022", api_key="ant-test",
        )
        assert "claude-3-5-sonnet-20241022" in provider.display_name()


# ---------------------------------------------------------------------------
# GroqProvider
# ---------------------------------------------------------------------------


class TestGroqProvider:
    def test_no_api_key_raises(self) -> None:
        with pytest.raises(ProviderAuthError, match="api_key"):
            GroqProvider(model="llama-3.3-70b-versatile", api_key="")

    def test_delegates_to_openai(self) -> None:
        provider = GroqProvider(
            model="llama-3.3-70b-versatile", api_key="gsk-test",
        )
        assert isinstance(provider._delegate, OpenAIProvider)
        assert "groq.com" in provider._delegate._base_url

    @pytest.mark.asyncio
    async def test_chat_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Fast response"},
                    "finish_reason": "stop",
                }
            ],
            "model": "llama-3.3-70b-versatile",
            "usage": {"prompt_tokens": 8, "completion_tokens": 3},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        provider = GroqProvider(
            model="llama-3.3-70b-versatile", api_key="gsk-test",
        )
        provider._delegate._client = mock_client

        resp = await provider.chat(SAMPLE_MESSAGES)
        assert resp.content == "Fast response"

    def test_display_name(self) -> None:
        provider = GroqProvider(
            model="llama-3.3-70b-versatile", api_key="gsk-test",
        )
        assert "llama-3.3-70b-versatile" in provider.display_name()


# ---------------------------------------------------------------------------
# from_image_config factory
# ---------------------------------------------------------------------------


class TestFromImageConfig:
    """ModelProvider.from_image_config() for image model isolation."""

    def test_provider_main_returns_ollama(self) -> None:
        cfg = _make_image_gen_config(image_model_provider="main")
        client = _make_ollama_client()
        provider = ModelProvider.from_image_config(cfg, client)
        assert isinstance(provider, OllamaProvider)

    def test_provider_main_without_client_raises(self) -> None:
        cfg = _make_image_gen_config(image_model_provider="main")
        with pytest.raises(ProviderError, match="OllamaClient"):
            ModelProvider.from_image_config(cfg, None)

    def test_provider_ollama_returns_ollama_with_model(self) -> None:
        cfg = _make_image_gen_config(
            image_model_provider="ollama",
            image_model_name="dolphin-mistral:7b",
        )
        client = _make_ollama_client()
        provider = ModelProvider.from_image_config(cfg, client)
        assert isinstance(provider, OllamaProvider)
        assert "dolphin-mistral:7b" in provider.display_name()

    def test_provider_ollama_without_client_raises(self) -> None:
        cfg = _make_image_gen_config(image_model_provider="ollama")
        with pytest.raises(ProviderError, match="OllamaClient"):
            ModelProvider.from_image_config(cfg, None)

    def test_provider_openai(self) -> None:
        cfg = _make_image_gen_config(
            image_model_provider="openai",
            image_model_name="gpt-4o",
            image_model_api_key="sk-test",
        )
        provider = ModelProvider.from_image_config(cfg)
        assert isinstance(provider, OpenAIProvider)

    def test_provider_anthropic(self) -> None:
        cfg = _make_image_gen_config(
            image_model_provider="anthropic",
            image_model_name="claude-3-5-sonnet-20241022",
            image_model_api_key="ant-test",
        )
        provider = ModelProvider.from_image_config(cfg)
        assert isinstance(provider, AnthropicProvider)

    def test_provider_groq(self) -> None:
        cfg = _make_image_gen_config(
            image_model_provider="groq",
            image_model_name="llama-3.3-70b-versatile",
            image_model_api_key="gsk-test",
        )
        provider = ModelProvider.from_image_config(cfg)
        assert isinstance(provider, GroqProvider)

    def test_unknown_provider_raises(self) -> None:
        cfg = _make_image_gen_config(image_model_provider="unknown_llm")
        with pytest.raises(ValueError, match="Unknown image_model_provider"):
            ModelProvider.from_image_config(cfg)


# ---------------------------------------------------------------------------
# ChatMessage / ChatResponse / ChatChunk data classes
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_chat_message_fields(self) -> None:
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_chat_response_defaults(self) -> None:
        resp = ChatResponse(content="ok")
        assert resp.finish_reason == "stop"
        assert resp.model == ""
        assert resp.usage == {}

    def test_chat_chunk_defaults(self) -> None:
        chunk = ChatChunk()
        assert chunk.content == ""
        assert chunk.done is False

    def test_chat_chunk_with_content(self) -> None:
        chunk = ChatChunk(content="hello", done=True)
        assert chunk.content == "hello"
        assert chunk.done is True


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrors:
    def test_provider_error_is_runtime_error(self) -> None:
        assert issubclass(ProviderError, RuntimeError)

    def test_connection_error_is_provider_error(self) -> None:
        assert issubclass(ProviderConnectionError, ProviderError)

    def test_auth_error_is_provider_error(self) -> None:
        assert issubclass(ProviderAuthError, ProviderError)

    def test_rate_limit_is_provider_error(self) -> None:
        assert issubclass(ProviderRateLimitError, ProviderError)

    def test_model_not_found_is_provider_error(self) -> None:
        assert issubclass(ProviderModelNotFoundError, ProviderError)
