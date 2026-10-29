"""Tests for jarvis.tools.local.focus_mode.

Uses temp directory for state storage. No real hosts file is modified.
"""

from __future__ import annotations

import re
import time
from unittest.mock import patch

import pytest

from jarvis.tools.local.focus_mode import (
    _DEFAULT_BLOCKED,
    FocusModeStatusArgs,
    FocusModeStatusTool,
    StartFocusModeArgs,
    StartFocusModeTool,
    StopFocusModeArgs,
    StopFocusModeTool,
    _load_state,
    _save_state,
    is_site_blocked,
)
from jarvis.tools.registry import VoicePattern

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    @pytest.mark.parametrize("cls,name,schema", [
        (StartFocusModeTool, "start_focus_mode", StartFocusModeArgs),
        (StopFocusModeTool, "stop_focus_mode", StopFocusModeArgs),
        (FocusModeStatusTool, "focus_mode_status", FocusModeStatusArgs),
    ])
    def test_has_required_attributes(self, cls, name, schema):
        tool = cls()
        assert tool.name == name
        assert tool.description
        assert tool.args_schema is schema
        assert tool.requires_confirmation is False

    @pytest.mark.parametrize("cls", [
        StartFocusModeTool, StopFocusModeTool, FocusModeStatusTool,
    ])
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
        "focus mode on",
        "focus mode start",
        "start focus mode",
        "work mode on",
        "focus mode on for 30 minutes",
        "block youtube for 2 hours",
        "focus mode off",
        "stop focus mode",
        "disable focus mode",
        "turn off focus mode",
        "focus mode status",
        "is focus mode on",
    ])
    def test_en_patterns_match(self, utterance):
        all_patterns = (
            StartFocusModeTool.voice_patterns
            + StopFocusModeTool.voice_patterns
            + FocusModeStatusTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "режим фокуса",
        "режим работы",
        "режим фокуса на 30 минут",
        "заблокируй ютуб на 2 часа",
        "выключи фокус",
        "выключи режим фокуса",
        "статус фокуса",
    ])
    def test_ru_patterns_match(self, utterance):
        all_patterns = (
            StartFocusModeTool.voice_patterns
            + StopFocusModeTool.voice_patterns
            + FocusModeStatusTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "режим фокусу",
        "режим роботи",
        "заблокуй ютуб на 2 години",
        "вимкни фокус",
        "статус фокусу",
    ])
    def test_uk_patterns_match(self, utterance):
        all_patterns = (
            StartFocusModeTool.voice_patterns
            + StopFocusModeTool.voice_patterns
            + FocusModeStatusTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# State storage
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _temp_state(tmp_path, monkeypatch):
    # Data dir resolved via jarvis.paths; repoint with JARVIS_DATA_DIR and
    # reset the lazy store. Layout unchanged: <data_dir>/data/focus_mode.json.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("jarvis.tools.local.focus_mode._FOCUS_STORE", None)


class TestStorage:
    def test_load_default(self):
        state = _load_state()
        assert state["active"] is False

    def test_save_and_load(self):
        state = {"active": True, "blocked": ["youtube.com"], "end_time": None, "mode": "soft"}
        _save_state(state)
        loaded = _load_state()
        assert loaded["active"] is True
        assert "youtube.com" in loaded["blocked"]


# ---------------------------------------------------------------------------
# is_site_blocked API
# ---------------------------------------------------------------------------

class TestIsSiteBlocked:
    def test_not_blocked_when_inactive(self):
        assert not is_site_blocked("https://youtube.com")

    def test_blocked_when_active(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com"],
            "end_time": time.time() + 3600,
            "mode": "soft",
        })
        assert is_site_blocked("https://youtube.com/watch?v=123")
        assert not is_site_blocked("https://github.com")

    def test_auto_expire(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com"],
            "end_time": time.time() - 10,  # Expired
            "mode": "soft",
        })
        assert not is_site_blocked("https://youtube.com")
        # State should be deactivated
        state = _load_state()
        assert state["active"] is False


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------

class TestStartFocusMode:
    @pytest.mark.asyncio
    async def test_start_default(self):
        tool = StartFocusModeTool()
        result = await tool.execute(StartFocusModeArgs())
        assert result.success
        assert "Focus mode ON" in result.output
        assert "60 minutes" in result.output
        # Verify state
        state = _load_state()
        assert state["active"] is True
        assert state["mode"] == "soft"
        assert len(state["blocked"]) == len(_DEFAULT_BLOCKED)

    @pytest.mark.asyncio
    async def test_start_custom_sites(self):
        tool = StartFocusModeTool()
        result = await tool.execute(StartFocusModeArgs(
            duration_minutes=30,
            sites=["twitch.tv", "discord.com"],
        ))
        assert result.success
        assert "30 minutes" in result.output
        state = _load_state()
        assert "twitch.tv" in state["blocked"]
        assert "discord.com" in state["blocked"]

    @pytest.mark.asyncio
    async def test_start_hosts_mode_no_admin(self):
        tool = StartFocusModeTool()
        with patch("jarvis.tools.local.focus_mode._has_admin", return_value=False):
            result = await tool.execute(StartFocusModeArgs(use_hosts=True))
            assert result.success
            state = _load_state()
            # Should fall back to soft mode
            assert state["mode"] == "soft"

    @pytest.mark.asyncio
    async def test_start_hosts_mode_with_admin(self):
        tool = StartFocusModeTool()
        with patch("jarvis.tools.local.focus_mode._has_admin", return_value=True):
            with patch("jarvis.tools.local.focus_mode._add_hosts_entries", return_value=None):
                result = await tool.execute(StartFocusModeArgs(use_hosts=True))
                assert result.success
                state = _load_state()
                assert state["mode"] == "hosts"


class TestStopFocusMode:
    @pytest.mark.asyncio
    async def test_stop_when_active(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com"],
            "end_time": time.time() + 3600,
            "mode": "soft",
        })
        tool = StopFocusModeTool()
        result = await tool.execute(StopFocusModeArgs())
        assert result.success
        assert "OFF" in result.output
        state = _load_state()
        assert state["active"] is False

    @pytest.mark.asyncio
    async def test_stop_when_inactive(self):
        tool = StopFocusModeTool()
        result = await tool.execute(StopFocusModeArgs())
        assert result.success
        assert "not active" in result.output

    @pytest.mark.asyncio
    async def test_stop_hosts_mode(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com"],
            "end_time": time.time() + 3600,
            "mode": "hosts",
        })
        tool = StopFocusModeTool()
        with patch("jarvis.tools.local.focus_mode._remove_hosts_entries", return_value=None):
            result = await tool.execute(StopFocusModeArgs())
            assert result.success


class TestFocusModeStatus:
    @pytest.mark.asyncio
    async def test_status_inactive(self):
        tool = FocusModeStatusTool()
        result = await tool.execute(FocusModeStatusArgs())
        assert result.success
        assert "not active" in result.output

    @pytest.mark.asyncio
    async def test_status_active(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com", "reddit.com"],
            "end_time": time.time() + 1800,  # 30 min
            "mode": "soft",
        })
        tool = FocusModeStatusTool()
        result = await tool.execute(FocusModeStatusArgs())
        assert result.success
        assert "ACTIVE" in result.output
        assert "minute" in result.output.lower()
        assert "youtube.com" in result.output

    @pytest.mark.asyncio
    async def test_status_expired(self):
        _save_state({
            "active": True,
            "blocked": ["youtube.com"],
            "end_time": time.time() - 10,
            "mode": "soft",
        })
        tool = FocusModeStatusTool()
        result = await tool.execute(FocusModeStatusArgs())
        assert result.success
        assert "ended" in result.output.lower()
