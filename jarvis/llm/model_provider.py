"""Universal LLM provider — unified interface for Ollama, OpenAI, Anthropic, Groq.

Coding Agent (and potentially other subsystems) need to talk to an LLM
that may be different from the main conversational model.  This module
provides a thin adapter layer: ``ModelProvider.from_config()`` reads a
``CodingConfig`` and returns the right concrete provider.

Usage::

    provider = ModelProvider.from_config(coding_config, ollama_client)
    response = await provider.chat(messages, temperature=0.2)
    # or streaming:
    async for chunk in provider.stream_chat(messages):
        print(chunk.content)

All API providers use httpx directly (no vendor SDKs) to keep
dependencies minimal. Error handling is unified into ``ProviderError``
subtypes so callers don't need provider-specific try/except.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from jarvis.core.config import CodingConfig, ImageGenConfig
    from jarvis.llm.ollama_client import OllamaClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single chat message in the universal format."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Complete (non-streaming) response from any provider."""

    content: str
    finish_reason: str = "stop"
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatChunk:
    """One streaming chunk from any provider."""

    content: str = ""
    done: bool = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProviderError(RuntimeError):
    """Base for all provider errors."""


class ProviderConnectionError(ProviderError):
    """Could not reach the provider endpoint."""


class ProviderAuthError(ProviderError):
    """Authentication failed (bad API key)."""


class ProviderRateLimitError(ProviderError):
    """Rate-limited by the API."""


class ProviderModelNotFoundError(ProviderError):
    """Requested model does not exist on the provider."""


# ---------------------------------------------------------------------------
# Default httpx timeout — generous read for slow LLMs.
# ---------------------------------------------------------------------------

def _api_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ModelProvider(ABC):
    """Unified interface for LLM chat completions."""

    @staticmethod
    def from_config(
        config: CodingConfig,
        ollama_client: OllamaClient | None = None,
    ) -> ModelProvider:
        """Factory: build the right provider from a CodingConfig."""
        provider = config.provider

        if provider == "main":
            if ollama_client is None:
                raise ProviderError(
                    "provider='main' requires an OllamaClient instance"
                )
            return OllamaProvider(client=ollama_client, model=None)

        if provider == "ollama":
            if ollama_client is None:
                raise ProviderError(
                    "provider='ollama' requires an OllamaClient instance"
                )
            return OllamaProvider(client=ollama_client, model=config.ollama_model)

        if provider == "openai":
            return OpenAIProvider(
                model=config.api_model or "gpt-4o",
                api_key=config.api_key,
                base_url=config.api_base_url or "https://api.openai.com/v1",
            )

        if provider == "anthropic":
            return AnthropicProvider(
                model=config.api_model or "claude-3-5-sonnet-20241022",
                api_key=config.api_key,
            )

        if provider == "groq":
            return GroqProvider(
                model=config.api_model or "llama-3.3-70b-versatile",
                api_key=config.api_key,
            )

        raise ValueError(f"Unknown provider: {provider!r}")

    @staticmethod
    def from_image_config(
        config: ImageGenConfig,
        ollama_client: OllamaClient | None = None,
    ) -> ModelProvider:
        """Factory: build a provider for image prompt engineering.

        Reads the ``image_model_*`` fields from an :class:`ImageGenConfig`
        and returns the appropriate concrete provider.  The returned
        provider is used *exclusively* for enhancing image prompts — it
        is loaded, used, and discarded within a single image-generation
        request so the main LLM never sees the prompt.

        Parameters
        ----------
        config:
            The ``image_gen`` section of :class:`JarvisConfig`.
        ollama_client:
            Required when ``image_model_provider`` is ``"ollama"`` or
            ``"main"`` (delegates to the existing ``OllamaClient``).

        Raises
        ------
        ProviderError
            If ``provider="ollama"``/``"main"`` and *ollama_client* is None.
        ValueError
            If the provider string is unrecognised.
        """
        provider = config.image_model_provider

        if provider == "main":
            if ollama_client is None:
                raise ProviderError(
                    "image_model_provider='main' requires an OllamaClient instance"
                )
            return OllamaProvider(client=ollama_client, model=None)

        if provider == "ollama":
            if ollama_client is None:
                raise ProviderError(
                    "image_model_provider='ollama' requires an OllamaClient instance"
                )
            return OllamaProvider(client=ollama_client, model=config.image_model_name)

        if provider == "openai":
            return OpenAIProvider(
                model=config.image_model_name or "gpt-4o",
                api_key=config.image_model_api_key,
                base_url=config.image_model_api_base_url or "https://api.openai.com/v1",
            )

        if provider == "anthropic":
            return AnthropicProvider(
                model=config.image_model_name or "claude-3-5-sonnet-20241022",
                api_key=config.image_model_api_key,
            )

        if provider == "groq":
            return GroqProvider(
                model=config.image_model_name or "llama-3.3-70b-versatile",
                api_key=config.image_model_api_key,
            )

        raise ValueError(f"Unknown image_model_provider: {provider!r}")

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Non-streaming chat completion."""

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ChatChunk]:
        """Streaming chat completion — yields ChatChunk values."""

    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider + model label for UI / logs."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _messages_to_dicts(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _raise_for_status(response: httpx.Response, provider_name: str) -> None:
    """Map HTTP errors to ProviderError subtypes."""
    if response.is_success:
        return
    code = response.status_code
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:500]}

    detail = body.get("error", {})
    if isinstance(detail, dict):
        msg = detail.get("message", str(body))
    else:
        msg = str(detail)

    if code == 401:
        raise ProviderAuthError(
            f"{provider_name}: authentication failed — check your API key. "
            f"({msg})"
        )
    if code == 429:
        raise ProviderRateLimitError(
            f"{provider_name}: rate limit exceeded. ({msg})"
        )
    if code == 404:
        raise ProviderModelNotFoundError(
            f"{provider_name}: model not found. ({msg})"
        )
    raise ProviderError(
        f"{provider_name}: HTTP {code} — {msg}"
    )


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

class OllamaProvider(ModelProvider):
    """Delegates to the existing OllamaClient for Ollama models."""

    def __init__(
        self,
        *,
        client: OllamaClient,
        model: str | None,
    ) -> None:
        self._client = client
        # None → use the client's own model (i.e. main LLM)
        self._model = model

    def display_name(self) -> str:
        return f"Ollama ({self._model or self._client.model})"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        full_text: list[str] = []
        async for chunk in self.stream_chat(
            messages, temperature=temperature, max_tokens=max_tokens,
        ):
            if chunk.content:
                full_text.append(chunk.content)
        return ChatResponse(
            content="".join(full_text),
            model=self._model or self._client.model,
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ChatChunk]:
        # Build messages in Ollama format
        ollama_msgs = _messages_to_dicts(messages)

        # If we have a custom model, temporarily swap the client's model
        original_model = self._client.model
        original_temp = self._client.temperature
        original_max = self._client.max_tokens

        try:
            if self._model is not None:
                self._client.model = self._model
            self._client.temperature = temperature
            self._client.max_tokens = max_tokens

            async for ollama_chunk in self._client.stream_chat(ollama_msgs):
                yield ChatChunk(
                    content=ollama_chunk.content,
                    done=ollama_chunk.done,
                )
        finally:
            self._client.model = original_model
            self._client.temperature = original_temp
            self._client.max_tokens = original_max


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------

class OpenAIProvider(ModelProvider):
    """OpenAI-compatible API (works with OpenAI, Azure, any compatible endpoint)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        if not api_key:
            raise ProviderAuthError("OpenAI: api_key is required")
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def display_name(self) -> str:
        return f"OpenAI ({self._model})"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=_api_timeout(),
            )
        return self._client

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        client = self._get_client()
        body = {
            "model": self._model,
            "messages": _messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            resp = await client.post("/chat/completions", json=body)
        except httpx.ConnectError as e:
            raise ProviderConnectionError(
                f"OpenAI: cannot connect to {self._base_url}"
            ) from e

        _raise_for_status(resp, "OpenAI")
        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return ChatResponse(
            content=choice["message"]["content"],
            finish_reason=choice.get("finish_reason", "stop"),
            model=data.get("model", self._model),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ChatChunk]:
        client = self._get_client()
        body = {
            "model": self._model,
            "messages": _messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            async with client.stream(
                "POST", "/chat/completions", json=body,
            ) as resp:
                _raise_for_status(resp, "OpenAI")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload == "[DONE]":
                        yield ChatChunk(done=True)
                        return
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield ChatChunk(content=content)
        except httpx.ConnectError as e:
            raise ProviderConnectionError(
                f"OpenAI: cannot connect to {self._base_url}"
            ) from e
        except GeneratorExit:
            raise

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

class AnthropicProvider(ModelProvider):
    """Anthropic Claude API."""

    API_BASE = "https://api.anthropic.com/v1"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("Anthropic: api_key is required")
        self._model = model
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    def display_name(self) -> str:
        return f"Anthropic ({self._model})"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.API_BASE,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": self.API_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=_api_timeout(),
            )
        return self._client

    def _build_body(
        self,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build the Anthropic /messages request body.

        Anthropic separates the system prompt from messages:
        ``system`` is a top-level string, ``messages`` must only
        contain user/assistant roles.
        """
        system_text = ""
        api_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_text = m.content
            else:
                api_messages.append({"role": m.role, "content": m.content})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if system_text:
            body["system"] = system_text
        return body

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        client = self._get_client()
        body = self._build_body(messages, temperature, max_tokens, stream=False)
        try:
            resp = await client.post("/messages", json=body)
        except httpx.ConnectError as e:
            raise ProviderConnectionError(
                f"Anthropic: cannot connect to {self.API_BASE}"
            ) from e

        _raise_for_status(resp, "Anthropic")
        data = resp.json()
        content_blocks = data.get("content", [])
        text = "".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return ChatResponse(
            content=text,
            finish_reason=data.get("stop_reason", "end_turn"),
            model=data.get("model", self._model),
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ChatChunk]:
        client = self._get_client()
        body = self._build_body(messages, temperature, max_tokens, stream=True)
        try:
            async with client.stream("POST", "/messages", json=body) as resp:
                _raise_for_status(resp, "Anthropic")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[len("data: "):])
                    except json.JSONDecodeError:
                        continue
                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            yield ChatChunk(content=text)
                    elif event_type == "message_stop":
                        yield ChatChunk(done=True)
                        return
        except httpx.ConnectError as e:
            raise ProviderConnectionError(
                f"Anthropic: cannot connect to {self.API_BASE}"
            ) from e
        except GeneratorExit:
            raise

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# GroqProvider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class GroqProvider(ModelProvider):
    """Groq API — OpenAI-compatible, just a different base_url."""

    GROQ_BASE = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("Groq: api_key is required")
        self._delegate = OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=self.GROQ_BASE,
        )

    def display_name(self) -> str:
        return f"Groq ({self._delegate._model})"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        return await self._delegate.chat(
            messages, temperature=temperature, max_tokens=max_tokens,
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ChatChunk]:
        async for chunk in self._delegate.stream_chat(
            messages, temperature=temperature, max_tokens=max_tokens,
        ):
            yield chunk

    async def close(self) -> None:
        await self._delegate.close()
