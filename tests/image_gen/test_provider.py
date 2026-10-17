"""Tests for jarvis.image_gen.provider — factory method and ABC."""

from __future__ import annotations

import pytest

from jarvis.core.config import ImageGenConfig
from jarvis.image_gen.provider import ImageProvider


class TestFactory:
    """ImageProvider.from_config() returns the right subclass."""

    def test_comfyui_provider(self):
        cfg = ImageGenConfig(provider="comfyui", local_model="test.safetensors")
        provider = ImageProvider.from_config(cfg)
        assert type(provider).__name__ == "ComfyUIProvider"

    def test_a1111_provider(self):
        cfg = ImageGenConfig(provider="a1111", local_model="test.safetensors")
        provider = ImageProvider.from_config(cfg)
        assert type(provider).__name__ == "A1111Provider"

    def test_openai_provider(self):
        cfg = ImageGenConfig(provider="openai", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        assert type(provider).__name__ == "OpenAIDalleProvider"

    def test_stability_provider(self):
        cfg = ImageGenConfig(provider="stability", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        assert type(provider).__name__ == "StabilityProvider"

    def test_replicate_provider(self):
        cfg = ImageGenConfig(provider="replicate", api_key="r8_test")
        provider = ImageProvider.from_config(cfg)
        assert type(provider).__name__ == "ReplicateProvider"

    def test_unknown_provider_raises(self):
        cfg = ImageGenConfig(provider="midjourney")
        with pytest.raises(ValueError, match="Unknown image generation provider"):
            ImageProvider.from_config(cfg)

    def test_disabled_raises(self):
        cfg = ImageGenConfig(provider="disabled")
        with pytest.raises(ValueError, match="Unknown image generation provider"):
            ImageProvider.from_config(cfg)


class TestProviderAvailability:
    """is_available() for API providers checks api_key."""

    def test_openai_available_with_key(self):
        cfg = ImageGenConfig(provider="openai", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        assert provider.is_available() is True

    def test_openai_unavailable_without_key(self):
        cfg = ImageGenConfig(provider="openai", api_key="")
        provider = ImageProvider.from_config(cfg)
        assert provider.is_available() is False

    def test_stability_available_with_key(self):
        cfg = ImageGenConfig(provider="stability", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        assert provider.is_available() is True

    def test_replicate_available_with_key(self):
        cfg = ImageGenConfig(provider="replicate", api_key="r8_test")
        provider = ImageProvider.from_config(cfg)
        assert provider.is_available() is True


class TestProviderListModels:
    """list_models() returns expected model lists for API providers."""

    async def test_openai_list_models(self):
        cfg = ImageGenConfig(provider="openai", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        models = await provider.list_models()
        assert "dall-e-3" in models
        assert "dall-e-2" in models

    async def test_stability_list_models(self):
        cfg = ImageGenConfig(provider="stability", api_key="sk-test")
        provider = ImageProvider.from_config(cfg)
        models = await provider.list_models()
        assert "stable-diffusion-xl-1024-v1-0" in models

    async def test_replicate_list_models(self):
        cfg = ImageGenConfig(provider="replicate", api_key="r8_test")
        provider = ImageProvider.from_config(cfg)
        models = await provider.list_models()
        assert len(models) > 0
