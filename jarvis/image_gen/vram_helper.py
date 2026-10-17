"""VRAM management for local image generation.

When ``auto_vram_swap`` is enabled, evicts the LLM from VRAM before
running a local Stable Diffusion generation, then reloads it after.
This prevents OOM on GPUs with limited VRAM (8–12 GB).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


async def with_vram_for_sd(
    vram_manager: object | None,
    ollama_client: object | None,
    func: Callable[[], Awaitable[T]],
) -> T:
    """Evict LLM from VRAM → run ``func`` → restore LLM.

    Parameters
    ----------
    vram_manager:
        Reserved for future VRAMManager integration. Currently unused;
        the eviction is done through ``ollama_client`` directly.
    ollama_client:
        The live OllamaClient instance. Must expose ``unload()`` and
        ``warm()`` async methods. If None, the function runs without
        VRAM management.
    func:
        The async callable that performs image generation.

    Returns the result of ``func()``.
    """
    if ollama_client is None:
        return await func()

    unload = getattr(ollama_client, "unload", None)
    warm = getattr(ollama_client, "warm", None)

    if callable(unload):
        try:
            log.info("VRAM swap: evicting LLM for image generation")
            await unload()
        except Exception:
            log.warning("VRAM swap: failed to evict LLM, proceeding anyway")

    try:
        result = await func()
    finally:
        if callable(warm):
            try:
                log.info("VRAM swap: restoring LLM after image generation")
                await warm()
            except Exception:
                log.warning("VRAM swap: failed to restore LLM")

    return result
