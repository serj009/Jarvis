"""Tests for SwitchLanguageTool — T2.2 manual language switching.

Tests voice patterns, STT language lock, TTS manager call, and
confirmation phrases in the target language.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.tools.local.switch_language import (
    SwitchLanguageArgs,
    SwitchLanguageTool,
    _CONFIRMATIONS,
)


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    """Voice patterns bypass the LLM and must match exactly."""

    @pytest.fixture
    def patterns(self):
        return SwitchLanguageTool.voice_patterns

    def _match(self, patterns, text: str) -> dict | None:
        """Simulate the router's first-match-wins pattern scan."""
        normalized = text.lower().strip()
        for vp in patterns:
            m = re.search(vp.regex, normalized, re.IGNORECASE)
            if m:
                return vp.args(m)
        return None

    # -- English --

    def test_speak_english(self, patterns):
        assert self._match(patterns, "speak english") == {"language": "en"}

    def test_switch_to_english(self, patterns):
        assert self._match(patterns, "switch to english") == {"language": "en"}

    def test_in_english(self, patterns):
        assert self._match(patterns, "in english") == {"language": "en"}

    def test_english_please(self, patterns):
        assert self._match(patterns, "english please") == {"language": "en"}

    # -- Russian --

    def test_govori_po_russki(self, patterns):
        assert self._match(patterns, "говори по-русски") == {"language": "ru"}

    def test_govori_po_russki_space(self, patterns):
        assert self._match(patterns, "говори по русски") == {"language": "ru"}

    def test_perekluchi_na_russkiy(self, patterns):
        assert self._match(patterns, "переключи на русский") == {"language": "ru"}

    def test_perekluchis_na_russkiy(self, patterns):
        assert self._match(patterns, "переключись на русский") == {"language": "ru"}

    def test_na_russkom(self, patterns):
        assert self._match(patterns, "на русском") == {"language": "ru"}

    # -- Ukrainian --

    def test_govory_ukrainskoyu(self, patterns):
        assert self._match(patterns, "говори українською") == {"language": "uk"}

    def test_perekluchi_na_ukrainsku(self, patterns):
        assert self._match(patterns, "переключи на українську") == {"language": "uk"}

    def test_po_ukrainsky(self, patterns):
        assert self._match(patterns, "по-українськи") == {"language": "uk"}

    def test_po_ukrainsky_space(self, patterns):
        assert self._match(patterns, "по українськи") == {"language": "uk"}

    # -- Auto --

    def test_auto_detect(self, patterns):
        assert self._match(patterns, "auto detect") == {"language": "auto"}

    def test_auto(self, patterns):
        assert self._match(patterns, "auto") == {"language": "auto"}

    def test_avtoopredeleniye(self, patterns):
        assert self._match(patterns, "автоопределение") == {"language": "auto"}

    def test_avtovyznachennya(self, patterns):
        assert self._match(patterns, "автовизначення") == {"language": "auto"}

    # -- No match --

    def test_no_match_random(self, patterns):
        assert self._match(patterns, "what time is it") is None

    def test_no_match_empty(self, patterns):
        assert self._match(patterns, "") is None


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

class TestSwitchLanguageExecution:
    """Test execute() wires STT and TTS manager correctly."""

    @pytest.fixture
    def mock_stt(self):
        stt = MagicMock()
        stt.language = "auto"
        stt.hotwords = ""
        return stt

    @pytest.fixture
    def mock_tts_manager(self):
        mgr = AsyncMock()
        mgr.set_language = AsyncMock()
        return mgr

    @pytest.mark.asyncio
    async def test_switch_to_russian(self, mock_stt, mock_tts_manager):
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="ru"))

        assert result.success is True
        assert mock_stt.language == "ru"
        mock_tts_manager.set_language.assert_awaited_once_with("ru")

    @pytest.mark.asyncio
    async def test_switch_to_english(self, mock_stt, mock_tts_manager):
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="en"))

        assert result.success is True
        assert mock_stt.language == "en"
        mock_tts_manager.set_language.assert_awaited_once_with("en")

    @pytest.mark.asyncio
    async def test_switch_to_ukrainian(self, mock_stt, mock_tts_manager):
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="uk"))

        assert result.success is True
        assert mock_stt.language == "uk"
        mock_tts_manager.set_language.assert_awaited_once_with("uk")

    @pytest.mark.asyncio
    async def test_switch_to_auto(self, mock_stt, mock_tts_manager):
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="auto"))

        assert result.success is True
        assert mock_stt.language == "auto"
        # Auto mode should NOT call tts_manager.set_language
        mock_tts_manager.set_language.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_hotwords(self, mock_stt, mock_tts_manager):
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        await tool.execute(SwitchLanguageArgs(language="uk"))

        # Hotwords should be updated to Ukrainian set
        from jarvis.audio.stt import HOTWORDS_UK
        assert mock_stt.hotwords == HOTWORDS_UK

    @pytest.mark.asyncio
    async def test_no_stt_instance(self, mock_tts_manager):
        """Tool works even without STT instance (skips STT switch)."""
        tool = SwitchLanguageTool(stt=None, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="ru"))

        assert result.success is True
        mock_tts_manager.set_language.assert_awaited_once_with("ru")

    @pytest.mark.asyncio
    async def test_no_tts_manager(self, mock_stt):
        """Tool works even without TTS manager (skips voice switch)."""
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=None)
        result = await tool.execute(SwitchLanguageArgs(language="ru"))

        assert result.success is True
        assert mock_stt.language == "ru"

    @pytest.mark.asyncio
    async def test_tts_failure_partial(self, mock_stt, mock_tts_manager):
        """TTS switch failure returns partial success."""
        mock_tts_manager.set_language.side_effect = RuntimeError("voice not found")
        tool = SwitchLanguageTool(stt=mock_stt, tts_manager=mock_tts_manager)
        result = await tool.execute(SwitchLanguageArgs(language="uk"))

        assert result.success is False
        assert "TTS" in result.error
        # STT should still be switched
        assert mock_stt.language == "uk"


# ---------------------------------------------------------------------------
# Confirmation phrases
# ---------------------------------------------------------------------------

class TestConfirmationPhrases:
    """Confirmations exist for all language combinations."""

    def test_all_languages_have_confirmations(self):
        for target in ("en", "ru", "uk", "auto"):
            assert target in _CONFIRMATIONS
            for lang in ("en", "ru", "uk"):
                assert lang in _CONFIRMATIONS[target]
                assert len(_CONFIRMATIONS[target][lang]) > 0
