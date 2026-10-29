"""Tests for jarvis.tools.local.image_gen_tool — voice patterns and execute."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch


from jarvis.core.config import ImageGenConfig
from jarvis.tools.local.image_gen_tool import ImageGenArgs, ImageGenTool

# ---------------------------------------------------------------------------
# Voice pattern tests
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    """ImageGenTool.voice_patterns match the expected voice commands."""

    @staticmethod
    def _match(text: str) -> str | None:
        """Try all patterns, return first group(1) match."""
        for vp in ImageGenTool.voice_patterns:
            m = re.search(vp.regex, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    # -- EN patterns --

    def test_en_generate_image_of(self):
        assert self._match("generate an image of a cat") == "a cat"

    def test_en_create_picture_of(self):
        assert self._match("create a picture of sunset") == "sunset"

    def test_en_draw_something(self):
        assert self._match("draw a dragon") == "a dragon"

    def test_en_make_photo_of(self):
        assert self._match("make a photo of mountains") == "mountains"

    def test_en_draw_me(self):
        assert self._match("draw me a spaceship") == "a spaceship"

    # -- RU patterns --

    def test_ru_narisuy(self):
        result = self._match("нарисуй кота")
        assert result == "кота"

    def test_ru_sgeneriruy(self):
        result = self._match("сгенерируй картинку заката")
        assert result == "заката"

    def test_ru_sozday(self):
        result = self._match("создай картинку дракона")
        assert result == "дракона"

    # -- UK patterns --

    def test_uk_namalyuy(self):
        result = self._match("намалюй кота")
        assert result == "кота"

    def test_uk_zgeneruy(self):
        result = self._match("згенеруй картинку заходу сонця")
        assert result == "заходу сонця"

    def test_uk_stvory(self):
        result = self._match("створи картинку дракона")
        assert result == "дракона"

    # -- Non-matches --

    def test_no_match_random(self):
        assert self._match("open chrome") is None

    def test_no_match_weather(self):
        assert self._match("what is the weather") is None


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------

class TestExecute:
    """ImageGenTool.execute() routing and error handling."""

    async def test_disabled_provider_returns_error(self):
        cfg = ImageGenConfig(provider="disabled")
        tool = ImageGenTool(image_gen_config=cfg)
        result = await tool.execute(ImageGenArgs(prompt="a cat"))
        assert not result.success
        assert "disabled" in result.error.lower()

    async def test_empty_provider_returns_error(self):
        cfg = ImageGenConfig(provider="")
        tool = ImageGenTool(image_gen_config=cfg)
        result = await tool.execute(ImageGenArgs(prompt="a cat"))
        assert not result.success

    async def test_successful_generation(self, tmp_path):
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test",
            api_model="dall-e-3",
            save_directory=str(tmp_path),
        )
        tool = ImageGenTool(image_gen_config=cfg)

        fake_path = tmp_path / "jarvis_dalle_test.png"
        fake_path.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=fake_path)

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_provider,
        ), patch("os.startfile", create=True):
            result = await tool.execute(ImageGenArgs(prompt="a sunset"))

        assert result.success
        assert "Картинка готова" in result.output

    async def test_generation_failure(self, tmp_path):
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test",
            save_directory=str(tmp_path),
        )
        tool = ImageGenTool(image_gen_config=cfg)

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(
            side_effect=RuntimeError("API error")
        )

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_provider,
        ):
            result = await tool.execute(ImageGenArgs(prompt="test"))

        assert not result.success
        assert "API error" in result.error

    async def test_vram_swap_for_local_provider(self, tmp_path):
        cfg = ImageGenConfig(
            provider="comfyui",
            local_endpoint="http://127.0.0.1:8188",
            local_model="test.safetensors",
            auto_vram_swap=True,
            save_directory=str(tmp_path),
        )
        fake_path = tmp_path / "test.png"
        fake_path.write_bytes(b"\x89PNG")

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=fake_path)

        mock_ollama = MagicMock()
        mock_ollama.unload = AsyncMock()
        mock_ollama.warm = AsyncMock()

        tool = ImageGenTool(
            image_gen_config=cfg, ollama_client=mock_ollama,
        )

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_provider,
        ), patch("os.startfile", create=True):
            result = await tool.execute(ImageGenArgs(prompt="test"))

        assert result.success
        mock_ollama.unload.assert_awaited_once()
        mock_ollama.warm.assert_awaited_once()


# ---------------------------------------------------------------------------
# Isolated image model tests
# ---------------------------------------------------------------------------

class TestIsolatedImageModel:
    """Tests for image_model_provider != 'main' (isolated mode)."""

    async def test_isolated_mode_uses_image_model(self, tmp_path):
        """When image_model_provider='ollama', prompt enhancement uses
        ModelProvider.from_image_config instead of the main model_provider."""
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test",
            api_model="dall-e-3",
            save_directory=str(tmp_path),
            enhance_mode="llm",
            image_model_provider="ollama",
            image_model_name="dolphin-mistral:7b",
        )

        fake_path = tmp_path / "test.png"
        fake_path.write_bytes(b"\x89PNG")

        mock_img_provider = AsyncMock()
        mock_img_provider.generate = AsyncMock(return_value=fake_path)

        # The image model provider returned by from_image_config
        mock_image_llm = MagicMock()

        # Track that from_image_config was called (not from_config)
        from_image_config_called = False

        def fake_from_image_config(config, ollama_client=None):
            nonlocal from_image_config_called
            from_image_config_called = True
            return mock_image_llm

        mock_ollama = MagicMock()
        mock_ollama.unload = AsyncMock()
        mock_ollama.warm = AsyncMock()

        tool = ImageGenTool(
            image_gen_config=cfg,
            ollama_client=mock_ollama,
            model_provider=MagicMock(),  # main provider — should NOT be used
        )

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_img_provider,
        ), patch(
            "jarvis.llm.model_provider.ModelProvider.from_image_config",
            side_effect=fake_from_image_config,
        ), patch(
            "jarvis.image_gen.prompt_enhancer.PromptEnhancer.enhance",
            new_callable=AsyncMock,
            return_value={"prompt": "enhanced", "negative_prompt": "bad"},
        ), patch("os.startfile", create=True):
            result = await tool.execute(ImageGenArgs(prompt="a cat"))

        assert result.success
        assert from_image_config_called

    async def test_isolated_mode_unloads_main_llm(self, tmp_path):
        """When image_model_provider='ollama', main LLM is unloaded
        before image model is used (VRAM swap)."""
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test",
            api_model="dall-e-3",
            save_directory=str(tmp_path),
            enhance_mode="llm",
            auto_vram_swap=True,
            image_model_provider="ollama",
            image_model_name="dolphin-mistral:7b",
        )

        fake_path = tmp_path / "test.png"
        fake_path.write_bytes(b"\x89PNG")

        mock_img_provider = AsyncMock()
        mock_img_provider.generate = AsyncMock(return_value=fake_path)

        mock_ollama = MagicMock()
        mock_ollama.unload = AsyncMock()
        mock_ollama.warm = AsyncMock()

        tool = ImageGenTool(
            image_gen_config=cfg,
            ollama_client=mock_ollama,
        )

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_img_provider,
        ), patch(
            "jarvis.llm.model_provider.ModelProvider.from_image_config",
            return_value=MagicMock(),
        ), patch(
            "jarvis.image_gen.prompt_enhancer.PromptEnhancer.enhance",
            new_callable=AsyncMock,
            return_value={"prompt": "enhanced", "negative_prompt": "bad"},
        ), patch("os.startfile", create=True):
            result = await tool.execute(ImageGenArgs(prompt="a cat"))

        assert result.success
        # unload called twice: once before image model, once after enhancement
        assert mock_ollama.unload.await_count == 2

    async def test_main_mode_uses_main_llm(self, tmp_path):
        """When image_model_provider='main', enhancement uses the main
        model_provider (backward compatibility)."""
        cfg = ImageGenConfig(
            provider="openai",
            api_key="sk-test",
            api_model="dall-e-3",
            save_directory=str(tmp_path),
            enhance_mode="llm",
            image_model_provider="main",  # default
        )

        fake_path = tmp_path / "test.png"
        fake_path.write_bytes(b"\x89PNG")

        mock_img_provider = AsyncMock()
        mock_img_provider.generate = AsyncMock(return_value=fake_path)

        main_provider = MagicMock()
        tool = ImageGenTool(
            image_gen_config=cfg,
            model_provider=main_provider,
        )

        with patch(
            "jarvis.tools.local.image_gen_tool.ImageProvider.from_config",
            return_value=mock_img_provider,
        ), patch(
            "jarvis.image_gen.prompt_enhancer.PromptEnhancer.enhance",
            new_callable=AsyncMock,
            return_value={"prompt": "enhanced", "negative_prompt": "bad"},
        ) as mock_enhance, patch("os.startfile", create=True):
            result = await tool.execute(ImageGenArgs(prompt="a cat"))

        assert result.success
        # PromptEnhancer should have been created with the main provider
        mock_enhance.assert_awaited_once()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    """ImageGenTool is properly registered in the catalogue."""

    def test_in_catalogue(self):
        from jarvis.tools.catalogue import local_tool_names
        assert "generate_image" in local_tool_names()

    def test_in_risk_map(self):
        from jarvis.tools.risk import TOOL_RISK_MAP, RiskLevel
        assert TOOL_RISK_MAP["generate_image"] == RiskLevel.WRITE

    def test_in_display_names(self):
        from jarvis.ui.settings.tabs.tools import TOOL_DISPLAY_NAMES
        assert "generate_image" in TOOL_DISPLAY_NAMES
