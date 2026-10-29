"""Adapter that presents a cloud-API LLM with the same interface as OllamaClient.

IntentRouter and all tools that consume ``ollama_client`` depend on two
methods: ``stream_chat(messages, *, tools=None)`` and ``vision_chat(...)``.
This module provides ``APILLMClient`` which has **exactly** the same
signatures but delegates to the universal ``ModelProvider`` / cloud API
under the hood.

Construction in ``app.py``::

    if cfg.llm.provider == "ollama":
        llm_client = OllamaClient(...)          # existing path, unchanged
    else:
        llm_client = APILLMClient.from_config(cfg.llm, cfg.vision)

IntentRouter, SeeScreenTool, and every other consumer never know the
difference — they call ``stream_chat`` / ``vision_chat`` as before.

For vision: when the vision provider is also an API provider, we call
the cloud API directly (GPT-4o, Claude etc. accept images inline).
When vision provider is "ollama" but LLM provider is API, we cannot
do vision through the API client — the caller (see_screen) will still
get an error that's clear enough.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jarvis.llm.model_provider import (
    AnthropicProvider,
    ChatMessage,
    GroqProvider,
    ModelProvider,
    OpenAIProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    _raise_for_status,
)
from jarvis.llm.ollama_client import ChatChunk

log = logging.getLogger(__name__)


class APILLMClient:
    """Drop-in replacement for OllamaClient that routes through cloud APIs.

    Quacks like OllamaClient: has ``stream_chat``, ``vision_chat``,
    ``load``, ``unload``, ``warm``, ``model``, ``temperature``,
    ``max_tokens``, ``system_prompt``, ``is_loaded``.
    """

    name: str = "api_llm_client"

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system_prompt: str = "",
        # Vision provider (may be None if vision uses Ollama separately)
        vision_provider: _VisionAPIProvider | None = None,
    ) -> None:
        self._provider = provider
        self.model = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.is_loaded: bool = False
        self._vision = vision_provider
        # keep_alive_seconds is checked by some code paths; no-op for API
        self.keep_alive_seconds: int = 0
        # num_ctx is referenced in some config-change handlers
        self.num_ctx: int = 16384

    @staticmethod
    def from_config(
        llm_cfg: Any,
        vision_cfg: Any | None = None,
    ) -> APILLMClient:
        """Build an APILLMClient from LLMConfig (and optionally VisionConfig).

        This is analogous to ``ModelProvider.from_config`` but builds the
        OllamaClient-compatible wrapper.
        """
        provider = _build_llm_provider(llm_cfg)

        vision_provider: _VisionAPIProvider | None = None
        if vision_cfg is not None and getattr(vision_cfg, "provider", "ollama") != "ollama":
            vision_provider = _VisionAPIProvider.from_config(vision_cfg)

        return APILLMClient(
            provider=provider,
            model_name=getattr(llm_cfg, "api_model", "") or "api-model",
            temperature=getattr(llm_cfg, "temperature", 0.7),
            max_tokens=getattr(llm_cfg, "max_tokens", 1024),
            system_prompt=getattr(llm_cfg, "system_prompt", ""),
            vision_provider=vision_provider,
        )

    # -- Loadable interface (no-ops for API) --

    async def load(self) -> None:
        self.is_loaded = True

    async def warm(self) -> None:
        """No warmup needed for API providers — just verify connectivity."""
        self.is_loaded = True
        log.info("API LLM client ready: %s", self._provider.display_name())

    async def unload(self) -> None:
        if hasattr(self._provider, "close"):
            try:
                await self._provider.close()
            except Exception:
                log.debug("API provider close failed", exc_info=True)
        self.is_loaded = False

    # -- streaming chat (same signature as OllamaClient) --

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Stream chat completion — same interface as OllamaClient.stream_chat.

        Note: ``tools`` parameter is accepted for interface compatibility
        but cloud-API tool calling is not implemented here (IntentRouter
        handles tool dispatch via its own pattern/LLM logic). The tools
        param is silently ignored.
        """
        # Convert dict messages to ChatMessage objects
        chat_messages = self._prepare_messages(messages)

        max_retries = 2
        backoff = 1.0

        for attempt in range(max_retries + 1):
            try:
                async for chunk in self._provider.stream_chat(
                    chat_messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                ):
                    yield ChatChunk(
                        content=chunk.content,
                        tool_calls=(),
                        done=chunk.done,
                    )
                return  # success — exit the retry loop
            except ProviderConnectionError as e:
                if attempt < max_retries:
                    log.warning(
                        "API connection failed (attempt %d/%d): %s",
                        attempt + 1, max_retries + 1, e,
                    )
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                log.error("API connection failed: %s", e)
                yield ChatChunk(
                    content=f"I couldn't reach the API, sir: {e}",
                    done=True,
                )
                return
            except ProviderRateLimitError as e:
                log.error("API rate-limited: %s", e)
                yield ChatChunk(
                    content="Too many requests, sir. Please wait a moment.",
                    done=True,
                )
                return
            except ProviderAuthError as e:
                log.error("API auth failed: %s", e)
                yield ChatChunk(
                    content=(
                        "API authentication failed, sir. "
                        "Please check your API key in Settings."
                    ),
                    done=True,
                )
                return
            except ProviderError as e:
                log.error("API error: %s", e)
                yield ChatChunk(
                    content=f"API error, sir: {e}",
                    done=True,
                )
                return

    # -- vision chat (same signature as OllamaClient) --

    async def vision_chat(
        self,
        *,
        model: str,
        prompt: str,
        image_b64: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> str:
        """One-shot vision chat — same interface as OllamaClient.vision_chat.

        When a vision API provider is configured, uses it. Otherwise raises
        an error (the caller, typically see_screen, will handle it).
        """
        if self._vision is None:
            raise RuntimeError(
                "No API vision provider configured. Set a vision provider "
                "in Settings → Models → Vision."
            )
        return await self._vision.vision_chat(
            prompt=prompt,
            image_b64=image_b64,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # -- internal --

    def _prepare_messages(self, messages: list[dict]) -> list[ChatMessage]:
        """Convert OpenAI/Ollama-format dicts to ChatMessage objects,
        prepending system prompt if needed."""
        result: list[ChatMessage] = []

        # Prepend system prompt if not already present
        if self.system_prompt and (
            not messages or messages[0].get("role") != "system"
        ):
            result.append(ChatMessage(role="system", content=self.system_prompt))

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            # Skip tool-result messages (not supported by base chat API)
            if role == "tool":
                continue
            result.append(ChatMessage(role=role, content=content))

        return result


# ---------------------------------------------------------------------------
# Vision API provider — handles image+text for cloud APIs
# ---------------------------------------------------------------------------

class _VisionAPIProvider:
    """Handles vision (image+text) requests via cloud APIs.

    OpenAI and Anthropic have different image-in-chat formats; this class
    abstracts both behind a single ``vision_chat`` method.
    """

    def __init__(
        self,
        *,
        provider_type: str,
        model: str,
        api_key: str,
        base_url: str = "",
    ) -> None:
        self._type = provider_type
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def from_config(vision_cfg: Any) -> _VisionAPIProvider:
        provider = getattr(vision_cfg, "provider", "ollama")
        model = getattr(vision_cfg, "api_model", "") or ""
        api_key = getattr(vision_cfg, "api_key", "") or ""
        base_url = getattr(vision_cfg, "api_base_url", "") or ""

        # Provide sensible model defaults
        if not model:
            if provider == "openai":
                model = "gpt-4o"
            elif provider == "anthropic":
                model = "claude-3-5-sonnet-20241022"

        return _VisionAPIProvider(
            provider_type=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}

            if self._type in ("openai", "groq"):
                base = self._base_url or (
                    "https://api.groq.com/openai/v1"
                    if self._type == "groq"
                    else "https://api.openai.com/v1"
                )
                headers["Authorization"] = f"Bearer {self._api_key}"
            elif self._type == "anthropic":
                base = "https://api.anthropic.com/v1"
                headers["x-api-key"] = self._api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                base = self._base_url or "https://api.openai.com/v1"
                headers["Authorization"] = f"Bearer {self._api_key}"

            self._client = httpx.AsyncClient(
                base_url=base,
                headers=headers,
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=15.0, pool=15.0),
            )
        return self._client

    async def vision_chat(
        self,
        *,
        prompt: str,
        image_b64: str,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> str:
        """Send image + prompt to the cloud API and return the text response."""
        if self._type == "anthropic":
            return await self._anthropic_vision(
                prompt=prompt,
                image_b64=image_b64,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        # OpenAI / Groq / OpenAI-compatible
        return await self._openai_vision(
            prompt=prompt,
            image_b64=image_b64,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _openai_vision(
        self,
        *,
        prompt: str,
        image_b64: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """OpenAI-style vision: image_url in content array."""
        client = self._get_client()
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = await client.post("/chat/completions", json=body)
        _raise_for_status(resp, f"Vision ({self._type})")
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    async def _anthropic_vision(
        self,
        *,
        prompt: str,
        image_b64: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Anthropic-style vision: base64 source in content array."""
        client = self._get_client()
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = await client.post("/messages", json=body)
        _raise_for_status(resp, "Vision (Anthropic)")
        data = resp.json()
        content_blocks = data.get("content", [])
        return "".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        ).strip()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Helper: build the right ModelProvider from an LLMConfig
# ---------------------------------------------------------------------------

def _build_llm_provider(llm_cfg: Any) -> ModelProvider:
    """Factory for the main LLM provider from LLMConfig fields."""
    provider = getattr(llm_cfg, "provider", "ollama")
    api_model = getattr(llm_cfg, "api_model", "") or ""
    api_key = getattr(llm_cfg, "api_key", "") or ""
    api_base_url = getattr(llm_cfg, "api_base_url", "") or ""

    if provider == "openai":
        return OpenAIProvider(
            model=api_model or "gpt-4o",
            api_key=api_key,
            base_url=api_base_url or "https://api.openai.com/v1",
        )

    if provider == "anthropic":
        return AnthropicProvider(
            model=api_model or "claude-3-5-sonnet-20241022",
            api_key=api_key,
        )

    if provider == "groq":
        return GroqProvider(
            model=api_model or "llama-3.3-70b-versatile",
            api_key=api_key,
        )

    raise ValueError(
        f"APILLMClient does not handle provider={provider!r}. "
        f"Use OllamaClient for provider='ollama'."
    )
