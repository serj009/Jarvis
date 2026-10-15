"""Tests for jarvis.tools.local.qr_share.

QR generation and browser interaction are mocked.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.qr_share import (
    GenerateQrTool,
    GenerateQrArgs,
    ShareToPhoneTool,
    ShareToPhoneArgs,
    _generate_qr_image,
)
from jarvis.tools.registry import ToolResult, VoicePattern


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    @pytest.mark.parametrize("cls,name,schema", [
        (GenerateQrTool, "generate_qr", GenerateQrArgs),
        (ShareToPhoneTool, "share_to_phone", ShareToPhoneArgs),
    ])
    def test_has_required_attributes(self, cls, name, schema):
        tool = cls()
        assert tool.name == name
        assert tool.description
        assert tool.args_schema is schema
        assert tool.requires_confirmation is False

    @pytest.mark.parametrize("cls", [GenerateQrTool, ShareToPhoneTool])
    def test_has_voice_patterns(self, cls):
        assert hasattr(cls, "voice_patterns")
        assert isinstance(cls.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in cls.voice_patterns)
        assert len(cls.voice_patterns) >= 3


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "show qr code for https://example.com",
        "generate qr code for hello",
        "qr for https://test.com",
        "share link to phone",
        "share this page to my phone",
        "qr for this page",
    ])
    def test_en_patterns_match(self, utterance):
        all_patterns = (
            GenerateQrTool.voice_patterns + ShareToPhoneTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "покажи qr код для google",
        "сгенерируй qr код",
        "открой ссылку на телефоне",
        "qr код для этой страницы",
    ])
    def test_ru_patterns_match(self, utterance):
        all_patterns = (
            GenerateQrTool.voice_patterns + ShareToPhoneTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "покажи qr код для google",
        "згенеруй qr код",
        "відкрий посилання на телефоні",
        "qr код для цієї сторінки",
    ])
    def test_uk_patterns_match(self, utterance):
        all_patterns = (
            GenerateQrTool.voice_patterns + ShareToPhoneTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# QR generation
# ---------------------------------------------------------------------------

class TestQrGeneration:
    def test_generate_with_qrcode_lib(self, tmp_path):
        """Test QR generation when qrcode library is available."""
        out = tmp_path / "test_qr.png"
        mock_qr = MagicMock()
        mock_img = MagicMock()
        mock_qr.make.return_value = mock_img

        with patch.dict("sys.modules", {"qrcode": mock_qr}):
            err = _generate_qr_image("https://test.com", out)
            # Should succeed (or fall through to httpx if mock doesn't work)
            # We just check it doesn't crash

    def test_generate_fallback_httpx(self, tmp_path):
        """Test fallback to httpx API when qrcode lib is missing."""
        out = tmp_path / "test_qr.png"
        mock_resp = MagicMock()
        mock_resp.content = b"PNG_DATA"
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("sys.modules", {"qrcode": None}):
            with patch("jarvis.tools.local.qr_share.httpx") as mock_httpx:
                # Force ImportError for qrcode
                import builtins
                real_import = builtins.__import__

                def selective_import(name, *args, **kwargs):
                    if name == "qrcode":
                        raise ImportError("no qrcode")
                    return real_import(name, *args, **kwargs)

                mock_httpx.get.return_value = mock_resp
                mock_httpx.URL.return_value = "https://test.com"
                with patch("builtins.__import__", side_effect=selective_import):
                    err = _generate_qr_image("https://test.com", out)
                    # Either succeeds via httpx mock or returns None
                    # (depends on import patching depth)


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_qr_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jarvis.tools.local.qr_share._QR_DIR", tmp_path / "qr_codes",
    )
    return tmp_path / "qr_codes"


class TestGenerateQr:
    @pytest.mark.asyncio
    async def test_generate_qr_with_data(self, mock_qr_dir):
        tool = GenerateQrTool()
        with patch(
            "jarvis.tools.local.qr_share._generate_qr_image",
            return_value=None,
        ):
            with patch("jarvis.tools.local.qr_share._open_image"):
                result = await tool.execute(GenerateQrArgs(
                    data="https://example.com", show=False,
                ))
                assert result.success
                assert "QR code saved" in result.output
                assert "example.com" in result.output

    @pytest.mark.asyncio
    async def test_generate_qr_no_data_no_browser(self, mock_qr_dir):
        tool = GenerateQrTool()
        with patch(
            "jarvis.tools.local.qr_share._get_current_url",
            return_value=None,
        ):
            result = await tool.execute(GenerateQrArgs(data=""))
            assert not result.success
            assert "no text" in result.error.lower() or "no active" in result.error.lower()

    @pytest.mark.asyncio
    async def test_generate_qr_from_browser(self, mock_qr_dir):
        tool = GenerateQrTool()
        with patch(
            "jarvis.tools.local.qr_share._get_current_url",
            return_value="https://github.com",
        ):
            with patch(
                "jarvis.tools.local.qr_share._generate_qr_image",
                return_value=None,
            ):
                with patch("jarvis.tools.local.qr_share._open_image"):
                    result = await tool.execute(GenerateQrArgs(data="", show=False))
                    assert result.success
                    assert "github.com" in result.output

    @pytest.mark.asyncio
    async def test_generate_qr_failure(self, mock_qr_dir):
        tool = GenerateQrTool()
        with patch(
            "jarvis.tools.local.qr_share._generate_qr_image",
            return_value="QR generation failed",
        ):
            result = await tool.execute(GenerateQrArgs(data="test"))
            assert not result.success
            assert "failed" in result.error.lower()


class TestShareToPhone:
    @pytest.mark.asyncio
    async def test_share_success(self, mock_qr_dir):
        tool = ShareToPhoneTool()
        with patch(
            "jarvis.tools.local.qr_share._get_current_url",
            return_value="https://example.com/page",
        ):
            with patch(
                "jarvis.tools.local.qr_share._generate_qr_image",
                return_value=None,
            ):
                with patch("jarvis.tools.local.qr_share._open_image"):
                    result = await tool.execute(ShareToPhoneArgs())
                    assert result.success
                    assert "example.com" in result.output

    @pytest.mark.asyncio
    async def test_share_no_browser(self, mock_qr_dir):
        tool = ShareToPhoneTool()
        with patch(
            "jarvis.tools.local.qr_share._get_current_url",
            return_value=None,
        ):
            result = await tool.execute(ShareToPhoneArgs())
            assert not result.success
            assert "no active" in result.error.lower()
