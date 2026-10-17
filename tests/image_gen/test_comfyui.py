"""Tests for jarvis.image_gen.comfyui — ComfyUI provider with mocked httpx."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.core.config import ImageGenConfig
from jarvis.image_gen.comfyui import ComfyUIProvider, _build_txt2img_workflow


class TestBuildWorkflow:
    """_build_txt2img_workflow() produces a valid ComfyUI node graph."""

    def test_workflow_has_required_nodes(self):
        wf = _build_txt2img_workflow(
            prompt="a cat",
            negative_prompt="blurry",
            model="sd_xl_base.safetensors",
            width=1024, height=1024, steps=20, cfg_scale=7.0, seed=42,
        )
        assert "3" in wf  # KSampler
        assert "4" in wf  # CheckpointLoaderSimple
        assert "5" in wf  # EmptyLatentImage
        assert "6" in wf  # CLIP positive
        assert "7" in wf  # CLIP negative
        assert "8" in wf  # VAEDecode
        assert "9" in wf  # SaveImage

    def test_workflow_model_name(self):
        wf = _build_txt2img_workflow(
            prompt="test", negative_prompt="", model="my_model.safetensors",
            width=512, height=512, steps=10, cfg_scale=5.0,
        )
        assert wf["4"]["inputs"]["ckpt_name"] == "my_model.safetensors"

    def test_workflow_dimensions(self):
        wf = _build_txt2img_workflow(
            prompt="test", negative_prompt="", model="m.safetensors",
            width=768, height=512, steps=10, cfg_scale=5.0,
        )
        assert wf["5"]["inputs"]["width"] == 768
        assert wf["5"]["inputs"]["height"] == 512

    def test_workflow_prompt_text(self):
        wf = _build_txt2img_workflow(
            prompt="cyberpunk city", negative_prompt="blurry, ugly",
            model="m.safetensors", width=1024, height=1024,
            steps=20, cfg_scale=7.0,
        )
        assert wf["6"]["inputs"]["text"] == "cyberpunk city"
        assert wf["7"]["inputs"]["text"] == "blurry, ugly"


class TestComfyUIProvider:
    """ComfyUIProvider with mocked HTTP responses."""

    def _make_provider(self, **kwargs) -> ComfyUIProvider:
        cfg = ImageGenConfig(
            provider="comfyui",
            local_endpoint="http://127.0.0.1:8188",
            local_model="test_model.safetensors",
            **kwargs,
        )
        return ComfyUIProvider(cfg)

    async def test_generate_success(self, tmp_path):
        provider = self._make_provider(save_directory=str(tmp_path))

        fake_prompt_resp = MagicMock()
        fake_prompt_resp.status_code = 200
        fake_prompt_resp.raise_for_status = MagicMock()
        fake_prompt_resp.json.return_value = {"prompt_id": "pid-123"}

        fake_history_resp = MagicMock()
        fake_history_resp.status_code = 200
        fake_history_resp.raise_for_status = MagicMock()
        fake_history_resp.json.return_value = {
            "pid-123": {
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "jarvis_00001.png",
                             "subfolder": "", "type": "output"}
                        ]
                    }
                }
            }
        }

        fake_view_resp = MagicMock()
        fake_view_resp.status_code = 200
        fake_view_resp.raise_for_status = MagicMock()
        fake_view_resp.content = b"\x89PNG\r\n\x1a\nFAKE"

        call_count = 0

        async def mock_post(url, **kwargs):
            return fake_prompt_resp

        async def mock_get(url, **kwargs):
            nonlocal call_count
            if "/history/" in url:
                call_count += 1
                return fake_history_resp
            return fake_view_resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("jarvis.image_gen.comfyui.httpx.AsyncClient", return_value=mock_client):
            result = await provider.generate(
                prompt="a cat", negative_prompt="blurry",
                width=1024, height=1024, steps=20, cfg_scale=7.0,
            )

        assert result.exists()
        assert result.suffix == ".png"
        assert result.parent == tmp_path

    async def test_list_models_success(self):
        provider = self._make_provider()

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [
                            ["model_a.safetensors", "model_b.safetensors"]
                        ]
                    }
                }
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("jarvis.image_gen.comfyui.httpx.AsyncClient", return_value=mock_client):
            models = await provider.list_models()

        assert models == ["model_a.safetensors", "model_b.safetensors"]

    def test_is_available_offline(self):
        provider = self._make_provider()
        # Without a running server, should return False
        assert provider.is_available() is False
