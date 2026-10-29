"""Tests for jarvis.image_gen.prompt_enhancer — template, LLM, styles, voice."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.image_gen.prompt_enhancer import (
    STYLE_NAMES,
    STYLES,
    PromptEnhancer,
    detect_style_from_text,
)

# ---------------------------------------------------------------------------
# Template mode
# ---------------------------------------------------------------------------

class TestTemplateEnhance:
    """_enhance_with_template adds quality tags and negative prompt."""

    async def test_basic_enhance(self):
        enhancer = PromptEnhancer(mode="template")
        result = await enhancer.enhance("a cat on a table", style="photorealistic")

        assert "a cat on a table" in result["prompt"]
        assert "masterpiece" in result["prompt"]
        assert "best quality" in result["prompt"]
        assert result["negative_prompt"]  # non-empty

    async def test_anime_style(self):
        enhancer = PromptEnhancer(mode="template")
        result = await enhancer.enhance("a warrior", style="anime")

        assert "a warrior" in result["prompt"]
        assert "anime" in result["prompt"].lower()
        assert result["negative_prompt"]

    async def test_all_styles_produce_output(self):
        enhancer = PromptEnhancer(mode="template")
        for style in STYLE_NAMES:
            result = await enhancer.enhance("test subject", style=style)
            assert result["prompt"], f"Style {style!r} produced empty prompt"
            assert result["negative_prompt"], f"Style {style!r} produced empty negative"

    async def test_unknown_style_falls_back_to_photorealistic(self):
        enhancer = PromptEnhancer(mode="template")
        result = await enhancer.enhance("test", style="nonexistent_style")
        # Should fallback to photorealistic (the default in STYLES.get)
        assert "masterpiece" in result["prompt"]

    async def test_returns_dict_with_both_keys(self):
        enhancer = PromptEnhancer(mode="template")
        result = await enhancer.enhance("anything")
        assert "prompt" in result
        assert "negative_prompt" in result


# ---------------------------------------------------------------------------
# LLM mode
# ---------------------------------------------------------------------------

class TestLLMEnhance:
    """_enhance_with_llm uses ModelProvider and falls back on error."""

    async def test_llm_enhance_success(self):
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "prompt": "a beautiful cat sitting on an oak table, golden hour lighting, 8k",
            "negative_prompt": "blurry, low quality, deformed",
        })
        mock_provider.chat = AsyncMock(return_value=mock_response)

        enhancer = PromptEnhancer(mode="llm", provider=mock_provider)
        result = await enhancer.enhance("cat on table", style="photorealistic")

        assert "beautiful cat" in result["prompt"]
        assert "blurry" in result["negative_prompt"]
        mock_provider.chat.assert_awaited_once()

    async def test_llm_enhance_fallback_on_error(self):
        """If LLM fails, falls back to template mode."""
        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("connection failed"))

        enhancer = PromptEnhancer(mode="llm", provider=mock_provider)
        result = await enhancer.enhance("cat on table", style="photorealistic")

        # Should get template-enhanced result (not crash)
        assert "cat on table" in result["prompt"]
        assert "masterpiece" in result["prompt"]
        assert result["negative_prompt"]

    async def test_llm_enhance_fallback_on_bad_json(self):
        """If LLM returns non-JSON, falls back to template."""
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all!"
        mock_provider.chat = AsyncMock(return_value=mock_response)

        enhancer = PromptEnhancer(mode="llm", provider=mock_provider)
        result = await enhancer.enhance("sunset", style="photorealistic")

        assert "sunset" in result["prompt"]
        assert "masterpiece" in result["prompt"]

    async def test_llm_without_provider_falls_back_to_template(self):
        """mode='llm' but provider=None → template fallback."""
        enhancer = PromptEnhancer(mode="llm", provider=None)
        result = await enhancer.enhance("a horse", style="anime")

        assert "a horse" in result["prompt"]
        assert "anime" in result["prompt"].lower()

    async def test_llm_system_prompt_includes_style(self):
        """The system prompt sent to LLM includes the requested style."""
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "prompt": "test", "negative_prompt": "test",
        })
        mock_provider.chat = AsyncMock(return_value=mock_response)

        enhancer = PromptEnhancer(mode="llm", provider=mock_provider)
        await enhancer.enhance("flower", style="watercolor")

        call_args = mock_provider.chat.call_args
        messages = call_args[0][0]
        system_msg = messages[0]
        assert "watercolor" in system_msg.content.lower()


# ---------------------------------------------------------------------------
# Enhance mode = "off" (via tool, not enhancer directly)
# ---------------------------------------------------------------------------

class TestEnhanceOff:
    """When enhance_mode is off, raw prompt should pass through."""

    async def test_template_mode_still_works_when_called(self):
        """PromptEnhancer doesn't have an 'off' mode itself; that's in the tool.
        But template mode with the raw prompt should still be predictable."""
        enhancer = PromptEnhancer(mode="template")
        result = await enhancer.enhance("raw input here")
        assert "raw input here" in result["prompt"]


# ---------------------------------------------------------------------------
# All styles have quality and negative
# ---------------------------------------------------------------------------

class TestStylesCompleteness:
    """Every built-in style must have 'quality' and 'negative' keys."""

    def test_all_styles_have_quality_and_negative(self):
        for name, preset in STYLES.items():
            assert "quality" in preset, f"Style {name!r} missing 'quality'"
            assert "negative" in preset, f"Style {name!r} missing 'negative'"
            assert len(preset["quality"]) > 10, f"Style {name!r} quality too short"
            assert len(preset["negative"]) > 10, f"Style {name!r} negative too short"

    def test_eight_styles_exist(self):
        assert len(STYLES) == 8

    def test_style_names_match_keys(self):
        assert STYLE_NAMES == list(STYLES.keys())


# ---------------------------------------------------------------------------
# Voice → style detection
# ---------------------------------------------------------------------------

class TestStyleDetectionFromVoice:
    """detect_style_from_text() finds the art style in voice input."""

    # RU patterns
    def test_ru_anime(self):
        style, cleaned = detect_style_from_text("нарисуй в стиле аниме кота")
        assert style == "anime"
        assert "кота" in cleaned

    def test_ru_photorealistic(self):
        style, cleaned = detect_style_from_text("в стиле реалистичном закат")
        assert style == "photorealistic"

    def test_ru_oil(self):
        style, cleaned = detect_style_from_text("в стиле масло пейзаж")
        assert style == "oil_painting"

    def test_ru_watercolor(self):
        style, cleaned = detect_style_from_text("в стиле акварель цветы")
        assert style == "watercolor"

    # UK patterns
    def test_uk_anime(self):
        style, cleaned = detect_style_from_text("в стилі аніме кіт")
        assert style == "anime"

    # EN patterns
    def test_en_anime_style(self):
        style, cleaned = detect_style_from_text("draw in anime style a cat")
        assert style == "anime"

    def test_en_realistic(self):
        style, cleaned = detect_style_from_text("draw a realistic sunset")
        assert style == "photorealistic"

    def test_en_watercolor(self):
        style, cleaned = detect_style_from_text("in the style of watercolor flowers")
        assert style == "watercolor"

    def test_en_oil_painting(self):
        style, cleaned = detect_style_from_text("in oil painting style mountains")
        assert style == "oil_painting"

    def test_en_pixel_art(self):
        style, cleaned = detect_style_from_text("in pixel art style a dragon")
        assert style == "pixel_art"

    def test_en_3d_render(self):
        style, cleaned = detect_style_from_text("in 3d render style a robot")
        assert style == "3d_render"

    def test_en_sketch(self):
        style, cleaned = detect_style_from_text("in sketch style a portrait")
        assert style == "sketch"

    def test_en_digital_art(self):
        style, cleaned = detect_style_from_text("in digital art style a landscape")
        assert style == "digital_art"

    # No style detected
    def test_no_style_returns_none(self):
        style, cleaned = detect_style_from_text("a beautiful sunset over mountains")
        assert style is None
        assert cleaned == "a beautiful sunset over mountains"

    def test_empty_string(self):
        style, cleaned = detect_style_from_text("")
        assert style is None
        assert cleaned == ""


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    """PromptEnhancer rejects unknown modes."""

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown enhance mode"):
            PromptEnhancer(mode="magic")

    def test_template_mode_no_provider(self):
        enhancer = PromptEnhancer(mode="template")
        assert enhancer._mode == "template"

    def test_llm_mode_with_provider(self):
        enhancer = PromptEnhancer(mode="llm", provider=MagicMock())
        assert enhancer._mode == "llm"


# ---------------------------------------------------------------------------
# Config fields
# ---------------------------------------------------------------------------

class TestConfigFields:
    """ImageGenConfig has enhance_mode and default_style."""

    def test_default_enhance_mode(self):
        from jarvis.core.config import ImageGenConfig
        cfg = ImageGenConfig()
        assert cfg.enhance_mode == "template"

    def test_default_style(self):
        from jarvis.core.config import ImageGenConfig
        cfg = ImageGenConfig()
        assert cfg.default_style == "photorealistic"

    def test_custom_enhance_mode(self):
        from jarvis.core.config import ImageGenConfig
        cfg = ImageGenConfig(enhance_mode="off")
        assert cfg.enhance_mode == "off"

    def test_custom_style(self):
        from jarvis.core.config import ImageGenConfig
        cfg = ImageGenConfig(default_style="anime")
        assert cfg.default_style == "anime"
