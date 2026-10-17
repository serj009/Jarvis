"""Abstract base class for image generation providers.

Every provider implements three methods:
  generate()      — txt2img → saved file → Path
  list_models()   — enumerate available models
  is_available()  — connectivity / readiness check

The static ``from_config()`` factory instantiates the right subclass
based on ``ImageGenConfig.provider``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

log = logging.getLogger(__name__)


class ImageProvider(ABC):
    """Unified interface for all image generation backends."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        cfg_scale: float = 7.0,
    ) -> Path:
        """Generate an image, save to file, return the file path."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """List available models (local) or supported models (API)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is reachable / configured."""

    @staticmethod
    def from_config(config: ImageGenConfig) -> ImageProvider:
        """Factory: build the right provider from config.provider string."""
        provider = config.provider.lower()

        if provider == "comfyui":
            from jarvis.image_gen.comfyui import ComfyUIProvider
            return ComfyUIProvider(config)
        elif provider == "a1111":
            from jarvis.image_gen.a1111 import A1111Provider
            return A1111Provider(config)
        elif provider == "openai":
            from jarvis.image_gen.openai_dalle import OpenAIDalleProvider
            return OpenAIDalleProvider(config)
        elif provider == "stability":
            from jarvis.image_gen.stability import StabilityProvider
            return StabilityProvider(config)
        elif provider == "replicate":
            from jarvis.image_gen.replicate import ReplicateProvider
            return ReplicateProvider(config)
        else:
            raise ValueError(
                f"Unknown image generation provider: {config.provider!r}. "
                f"Expected one of: comfyui, a1111, openai, stability, replicate, disabled."
            )
