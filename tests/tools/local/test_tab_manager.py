"""Tests for jarvis.tools.local.tab_manager.

All Selenium interactions are mocked — no real browser is launched.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from jarvis.tools.local.tab_manager import (
    CloseOtherTabsArgs,
    CloseOtherTabsTool,
    CountTabsArgs,
    CountTabsTool,
    DuplicateTabArgs,
    DuplicateTabTool,
    FindTabArgs,
    FindTabTool,
)
from jarvis.tools.registry import ToolResult, VoicePattern

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    @pytest.mark.parametrize("cls,name,schema", [
        (CloseOtherTabsTool, "close_other_tabs", CloseOtherTabsArgs),
        (CountTabsTool, "count_tabs", CountTabsArgs),
        (FindTabTool, "find_tab", FindTabArgs),
        (DuplicateTabTool, "duplicate_tab", DuplicateTabArgs),
    ])
    def test_has_required_attributes(self, cls, name, schema):
        tool = cls()
        assert tool.name == name
        assert tool.description
        assert tool.args_schema is schema
        assert tool.requires_confirmation is False

    @pytest.mark.parametrize("cls", [
        CloseOtherTabsTool, CountTabsTool, FindTabTool, DuplicateTabTool,
    ])
    def test_has_voice_patterns(self, cls):
        assert hasattr(cls, "voice_patterns")
        assert isinstance(cls.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in cls.voice_patterns)
        # At least 3 patterns per tool
        assert len(cls.voice_patterns) >= 3


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "close other tabs",
        "close all other tabs",
        "close tabs except this one",
        "close all tabs except current",
    ])
    def test_en_close_other_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in CloseOtherTabsTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "how many tabs open",
        "how many tabs are open",
        "count tabs",
        "count my open tabs",
    ])
    def test_en_count_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in CountTabsTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "find tab with youtube",
        "find the tab with github",
        "switch to the gmail tab",
    ])
    def test_en_find_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in FindTabTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "duplicate tab",
        "duplicate this tab",
    ])
    def test_en_duplicate_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in DuplicateTabTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "закрой остальные вкладки",
        "закрой все остальные вкладки",
        "сколько вкладок открыто",
        "найди вкладку с ютубом",
        "дублируй вкладку",
        "дублируй эту вкладку",
    ])
    def test_ru_patterns_match(self, utterance):
        all_patterns = (
            CloseOtherTabsTool.voice_patterns
            + CountTabsTool.voice_patterns
            + FindTabTool.voice_patterns
            + DuplicateTabTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "закрий інші вкладки",
        "скільки вкладок відкрито",
        "знайди вкладку з ютубом",
        "дублюй вкладку",
    ])
    def test_uk_patterns_match(self, utterance):
        all_patterns = (
            CloseOtherTabsTool.voice_patterns
            + CountTabsTool.voice_patterns
            + FindTabTool.voice_patterns
            + DuplicateTabTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# Mock browser helpers
# ---------------------------------------------------------------------------

def _make_mock_driver(tabs=None, current_idx=0):
    """Create a mock Selenium driver with window handles."""
    if tabs is None:
        tabs = [
            {"handle": "h1", "title": "YouTube", "url": "https://youtube.com"},
            {"handle": "h2", "title": "GitHub", "url": "https://github.com"},
            {"handle": "h3", "title": "Gmail", "url": "https://gmail.com"},
        ]
    driver = MagicMock()
    driver.window_handles = [t["handle"] for t in tabs]
    driver.current_window_handle = tabs[current_idx]["handle"]

    # Track which tab is active
    active = {"idx": current_idx}

    def switch_to_window(handle):
        for i, t in enumerate(tabs):
            if t["handle"] == handle:
                active["idx"] = i
                break

    driver.switch_to.window = switch_to_window
    type(driver).title = PropertyMock(side_effect=lambda: tabs[active["idx"]]["title"])
    type(driver).current_url = PropertyMock(side_effect=lambda: tabs[active["idx"]]["url"])

    return driver


@pytest.fixture()
def mock_browser():
    bc = MagicMock()
    bc.driver_active = True
    bc.driver = _make_mock_driver()
    with patch("jarvis.tools.local.tab_manager._get_browser", return_value=bc):
        yield bc


@pytest.fixture()
def mock_browser_no_selenium():
    bc = MagicMock()
    bc.driver_active = False
    with patch("jarvis.tools.local.tab_manager._get_browser", return_value=bc):
        yield bc


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------

class TestCloseOtherTabs:
    @pytest.mark.asyncio
    async def test_close_others(self, mock_browser):
        tool = CloseOtherTabsTool()
        result = await tool.execute(CloseOtherTabsArgs())
        assert result.success
        assert "2" in result.output  # Closed 2 tabs

    @pytest.mark.asyncio
    async def test_no_selenium_fallback(self, mock_browser_no_selenium):
        tool = CloseOtherTabsTool()
        result = await tool.execute(CloseOtherTabsArgs())
        assert not result.success
        assert "selenium" in result.error.lower()


class TestCountTabs:
    @pytest.mark.asyncio
    async def test_count(self, mock_browser):
        tool = CountTabsTool()
        result = await tool.execute(CountTabsArgs())
        assert result.success
        assert "3" in result.output

    @pytest.mark.asyncio
    async def test_no_selenium(self, mock_browser_no_selenium):
        tool = CountTabsTool()
        result = await tool.execute(CountTabsArgs())
        assert not result.success


class TestFindTab:
    @pytest.mark.asyncio
    async def test_find_by_title(self, mock_browser):
        tool = FindTabTool()
        result = await tool.execute(FindTabArgs(query="GitHub"))
        assert result.success
        assert "GitHub" in result.output

    @pytest.mark.asyncio
    async def test_find_by_url(self, mock_browser):
        tool = FindTabTool()
        result = await tool.execute(FindTabArgs(query="gmail"))
        assert result.success
        assert "gmail" in result.output.lower()

    @pytest.mark.asyncio
    async def test_find_not_found(self, mock_browser):
        tool = FindTabTool()
        result = await tool.execute(FindTabArgs(query="nonexistent"))
        assert not result.success
        assert "no tab" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_selenium(self, mock_browser_no_selenium):
        tool = FindTabTool()
        result = await tool.execute(FindTabArgs(query="test"))
        assert not result.success


class TestDuplicateTab:
    @pytest.mark.asyncio
    async def test_duplicate(self, mock_browser):
        tool = DuplicateTabTool()
        result = await tool.execute(DuplicateTabArgs())
        assert result.success
        assert "Duplicated" in result.output

    @pytest.mark.asyncio
    async def test_no_selenium(self, mock_browser_no_selenium):
        tool = DuplicateTabTool()
        result = await tool.execute(DuplicateTabArgs())
        assert not result.success
