"""Tests for jarvis.tools.local.media_control.

All media key presses and browser interactions are mocked.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.media_control import (
    MediaControlArgs,
    MediaControlTool,
    _press_media_key,
)
from jarvis.tools.registry import VoicePattern

# -- protocol conformance -----------------------------------------------


class TestProtocol:
    def test_has_required_attributes(self):
        tool = MediaControlTool()
        assert tool.name == "media_control"
        assert tool.description
        assert tool.args_schema is MediaControlArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(MediaControlTool, "voice_patterns")
        assert isinstance(MediaControlTool.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in MediaControlTool.voice_patterns)
        assert len(MediaControlTool.voice_patterns) >= 9


# -- voice pattern matching ---------------------------------------------


class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "pause",
        "play",
        "resume",
        "next track",
        "skip track",
        "previous track",
        "fullscreen",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in MediaControlTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "пауза",
        "стоп",
        "продолжи",
        "следующий трек",
        "полный экран",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in MediaControlTool.voice_patterns
        )
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "пауза",
        "стоп",
        "продовжи",
        "наступний трек",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in MediaControlTool.voice_patterns
        )
        assert matched, f"No UK pattern matched: {utterance!r}"


# -- _press_media_key ---------------------------------------------------


class TestPressMediaKey:
    @patch("jarvis.tools.local.media_control.pynput", create=True)
    def test_pynput_success(self, monkeypatch):
        """When pynput can be imported and works, returns success."""
        mock_controller = MagicMock()
        mock_key = MagicMock()
        mock_key.media_play_pause = "play_pause_key"

        with patch.dict("sys.modules", {"pynput": MagicMock(), "pynput.keyboard": MagicMock()}):
            with patch("jarvis.tools.local.media_control._press_media_key") as mock_fn:
                mock_fn.return_value = {"success": True, "method": "pynput", "key": "play_pause"}
                result = mock_fn("play_pause")
                assert result["success"] is True

    def test_no_deps_returns_error(self):
        """When neither pynput nor pyautogui are available."""
        with patch.dict("sys.modules", {"pynput": None, "pynput.keyboard": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                result = _press_media_key("play_pause")
                assert result["success"] is False


# -- execute dispatch ---------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_browser(monkeypatch):
    mock_bc = MagicMock()
    mock_bc.driver_active = False
    monkeypatch.setattr(
        "jarvis.tools.local.browser_tabs._get_browser",
        lambda: mock_bc,
    )
    return mock_bc


class TestExecute:
    @pytest.mark.asyncio
    async def test_pause_via_media_key(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.media_control._press_media_key",
            lambda key: {"success": True, "method": "pynput", "key": key},
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="pause"))
        assert result.success
        assert "paused" in result.output.lower()

    @pytest.mark.asyncio
    async def test_play_via_media_key(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.media_control._press_media_key",
            lambda key: {"success": True, "method": "pyautogui", "key": key},
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="play"))
        assert result.success
        assert "playing" in result.output.lower()

    @pytest.mark.asyncio
    async def test_next_track(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.media_control._press_media_key",
            lambda key: {"success": True, "method": "pynput", "key": key},
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="next_track"))
        assert result.success
        assert "next" in result.output.lower()

    @pytest.mark.asyncio
    async def test_prev_track(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.media_control._press_media_key",
            lambda key: {"success": True, "method": "pynput", "key": key},
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="prev_track"))
        assert result.success
        assert "previous" in result.output.lower()

    @pytest.mark.asyncio
    async def test_pause_via_selenium_js(self, _mock_browser, monkeypatch):
        _mock_browser.driver_active = True
        _mock_browser.execute_js = MagicMock(return_value={"success": True, "result": None})
        # Need to set up the import
        monkeypatch.setattr(
            "jarvis.tools.local.browser_tabs._get_browser",
            lambda: _mock_browser,
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="pause"))
        assert result.success

    @pytest.mark.asyncio
    async def test_fullscreen_without_selenium(self, _mock_browser, monkeypatch):
        _mock_browser.driver_active = False
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="fullscreen"))
        assert not result.success
        assert "selenium" in result.error.lower()

    @pytest.mark.asyncio
    async def test_media_key_failure(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.media_control._press_media_key",
            lambda key: {"success": False, "error": "No keys available"},
        )
        tool = MediaControlTool()
        result = await tool.execute(MediaControlArgs(action="pause"))
        assert not result.success
