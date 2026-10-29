"""Tests for jarvis.tools.local.password_gen.

Tests password generation, passphrase mode, clipboard, and constraints.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from jarvis.tools.local.password_gen import (
    _AMBIGUOUS,
    _PASSPHRASE_WORDS,
    GeneratePasswordArgs,
    GeneratePasswordTool,
)
from jarvis.tools.registry import VoicePattern

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_has_required_attributes(self):
        tool = GeneratePasswordTool()
        assert tool.name == "generate_password"
        assert tool.description
        assert tool.args_schema is GeneratePasswordArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(GeneratePasswordTool, "voice_patterns")
        assert isinstance(GeneratePasswordTool.voice_patterns, tuple)
        assert all(
            isinstance(p, VoicePattern)
            for p in GeneratePasswordTool.voice_patterns
        )
        assert len(GeneratePasswordTool.voice_patterns) >= 9


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "generate password",
        "generate a password",
        "generate a secure password",
        "generate password 20 chars",
        "random password 16 characters",
        "create a passphrase",
        "create passphrase 5 words",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in GeneratePasswordTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "сгенерируй пароль",
        "сгенерируй мне пароль",
        "случайный пароль",
        "случайный пароль 20 символов",
        "придумай пароль",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in GeneratePasswordTool.voice_patterns
        )
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "згенеруй пароль",
        "згенеруй мені пароль",
        "випадковий пароль",
        "випадковий пароль 16 символів",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in GeneratePasswordTool.voice_patterns
        )
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# Password generation logic
# ---------------------------------------------------------------------------

class TestRandomPassword:
    @pytest.mark.asyncio
    async def test_default_length(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=16, copy_to_clipboard=False,
        ))
        assert result.success
        # Extract password from output
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert len(pwd) == 16

    @pytest.mark.asyncio
    async def test_custom_length(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=32, copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert len(pwd) == 32

    @pytest.mark.asyncio
    async def test_no_uppercase(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=50, use_uppercase=False,
            use_digits=False, use_special=False,
            copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert pwd == pwd.lower()

    @pytest.mark.asyncio
    async def test_no_special(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=50, use_special=False,
            copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        specials = set("!@#$%^&*()-_=+[]{}|;:,.<>?")
        assert not any(c in specials for c in pwd)

    @pytest.mark.asyncio
    async def test_exclude_ambiguous(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=100, exclude_ambiguous=True,
            copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert not any(c in _AMBIGUOUS for c in pwd)

    @pytest.mark.asyncio
    async def test_min_length_enforced(self):
        tool = GeneratePasswordTool()
        # length=3 is < 8 for random mode — should error
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=3, copy_to_clipboard=False,
        ))
        assert not result.success
        assert "minimum" in result.error.lower()


class TestPassphrase:
    @pytest.mark.asyncio
    async def test_default_passphrase(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="passphrase", length=4, copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        words = pwd.split("-")
        assert len(words) == 4
        # All words should be from the word list
        for w in words:
            assert w in _PASSPHRASE_WORDS

    @pytest.mark.asyncio
    async def test_custom_separator(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="passphrase", length=3, separator="_",
            copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert "_" in pwd
        assert len(pwd.split("_")) == 3

    @pytest.mark.asyncio
    async def test_passphrase_word_count(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="passphrase", length=6, copy_to_clipboard=False,
        ))
        assert result.success
        pwd = result.output.split(":", 1)[1].split("\n")[0].strip()
        assert len(pwd.split("-")) == 6


class TestClipboard:
    @pytest.mark.asyncio
    async def test_clipboard_copy_pyperclip(self):
        tool = GeneratePasswordTool()
        with patch("jarvis.tools.local.password_gen.GeneratePasswordTool._copy_to_clipboard") as mock_copy:
            mock_copy.return_value = " Copied to clipboard."
            result = await tool.execute(GeneratePasswordArgs(
                mode="random", length=16, copy_to_clipboard=True,
            ))
            assert result.success
            assert "Copied" in result.output
            mock_copy.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_clipboard(self):
        tool = GeneratePasswordTool()
        result = await tool.execute(GeneratePasswordArgs(
            mode="random", length=16, copy_to_clipboard=False,
        ))
        assert result.success
        assert "Copied" not in result.output
