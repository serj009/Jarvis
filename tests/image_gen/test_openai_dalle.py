"""Tests for jarvis.image_gen.openai_dalle — OpenAI DALL-E provider with mocked httpx."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.core.config import ImageGenConfig
from jarvis.image_gen.openai_dalle import OpenAIDalleProvider


class TestOpenAIDalleProvider:
    """OpenAIDalleProvider with mocked HTTP responses."""

    def _make_provider(self, **kwargs) -> OpenAIDalleProvider:
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test-key",
            api_model="dall-e-3",
            **kwargs,
        )
        return OpenAIDalleProvider(cfg)

    async def test_generate_success(self, tmp_path):
        provider = self._make_provider(save_directory=str(tmp_path))

        fake_png = b"\x89PNG\r\n\x1a\nFAKEPNG"
        fake_b64 = base64.b64encode(fake_png).decode()

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "data": [{"b64_json": fake_b64}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("jarvis.image_gen.openai_dalle.httpx.AsyncClient", return_value=mock_client):
            result = await provider.generate(
                prompt="a sunset over mountains",
                width=1024, height=1024,
            )

        assert result.exists()
        assert result.suffix == ".png"
        assert result.read_bytes() == fake_png

    async def test_generate_no_key_raises(self, tmp_path):
        cfg = ImageGenConfig(
            provider="openai", api_key="",
            save_directory=str(tmp_path),
        )
        provider = OpenAIDalleProvider(cfg)

        with pytest.raises(RuntimeError, match="OpenAI API key not configured"):
            await provider.generate(prompt="test")

    async def test_generate_with_negative_prompt(self, tmp_path):
        provider = self._make_provider(save_directory=str(tmp_path))

        fake_png = b"\x89PNG\r\n\x1a\nFAKE"
        fake_b64 = base64.b64encode(fake_png).decode()

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "data": [{"b64_json": fake_b64}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("jarvis.image_gen.openai_dalle.httpx.AsyncClient", return_value=mock_client):
            result = await provider.generate(
                prompt="a cat", negative_prompt="dogs, blurry",
            )

        # Verify the prompt included the negative prompt
        call_kwargs = mock_client.post.await_args.kwargs
        payload = call_kwargs["json"]
        assert "Avoid: dogs, blurry" in payload["prompt"]


class TestResolveSizes:
    """_resolve_size() maps dimensions to DALL-E-supported sizes."""

    def test_square(self):
        assert OpenAIDalleProvider._resolve_size(1024, 1024) == "1024x1024"

    def test_landscape(self):
        assert OpenAIDalleProvider._resolve_size(1920, 1080) == "1792x1024"

    def test_portrait(self):
        assert OpenAIDalleProvider._resolve_size(768, 1344) == "1024x1792"

    def test_near_square(self):
        assert OpenAIDalleProvider._resolve_size(1024, 768) == "1024x1024"


class TestListModels:
    """list_models() returns the supported models."""

    async def test_list_models(self):
        cfg = ImageGenConfig(provider="openai", api_key="sk-test")
        provider = OpenAIDalleProvider(cfg)
        models = await provider.list_models()
        assert "dall-e-3" in models
        assert "dall-e-2" in models
