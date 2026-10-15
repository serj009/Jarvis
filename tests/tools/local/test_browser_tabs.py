"""Tests for jarvis.tools.local.browser_tabs.

All browser interactions are mocked — no real browser is launched.
"""

from __future__ import annotations

import re
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.browser_tabs import BrowserTabsTool, BrowserTabsArgs
from jarvis.tools.registry import ToolResult, VoicePattern


# -- protocol conformance -----------------------------------------------

class TestProtocol:
    def test_has_required_attributes(self):
        tool = BrowserTabsTool()
        assert tool.name == "browser_tabs"
        assert tool.description
        assert tool.args_schema is BrowserTabsArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(BrowserTabsTool, "voice_patterns")
        assert isinstance(BrowserTabsTool.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in BrowserTabsTool.voice_patterns)

    def test_minimum_nine_patterns(self):
        """Spec requires at least 9 patterns (3 EN + 3 RU + 3 UK)."""
        assert len(BrowserTabsTool.voice_patterns) >= 9


# -- voice pattern matching ---------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "open new tab",
        "new tab",
        "close tab",
        "close this tab",
        "switch tab 3",
        "tab 2",
        "back",
        "go back",
        "forward",
        "go forward",
        "refresh",
        "refresh page",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BrowserTabsTool.voice_patterns
        )
        assert matched, f"No pattern matched EN: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "открой новую вкладку",
        "закрой вкладку",
        "назад",
        "вперёд",
        "обнови страницу",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BrowserTabsTool.voice_patterns
        )
        assert matched, f"No pattern matched RU: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "відкрий нову вкладку",
        "закрий вкладку",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BrowserTabsTool.voice_patterns
        )
        assert matched, f"No pattern matched UK: {utterance!r}"


# -- execute dispatch ---------------------------------------------------

def _make_mock_browser(**overrides):
    bc = MagicMock()
    bc.open_tab.return_value = {"success": True, "url": "https://x.com", "mode": "selenium", "tab_count": 2}
    bc.close_tab.return_value = {"success": True, "remaining_tabs": 1}
    bc.switch_tab.return_value = {"success": True, "tab": 0, "title": "Page"}
    bc.go_back.return_value = {"success": True, "action": "back"}
    bc.go_forward.return_value = {"success": True, "action": "forward"}
    bc.refresh.return_value = {"success": True, "action": "refresh"}
    bc.list_tabs.return_value = {"success": True, "tabs": [], "count": 0}
    for k, v in overrides.items():
        setattr(bc, k, v)
    return bc


@pytest.fixture(autouse=True)
def _mock_browser(monkeypatch):
    mock_bc = _make_mock_browser()
    monkeypatch.setattr(
        "jarvis.tools.local.browser_tabs._get_browser",
        lambda: mock_bc,
    )
    return mock_bc


class TestExecute:
    @pytest.mark.asyncio
    async def test_new_tab(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="new_tab", url="https://x.com"))
        assert result.success
        assert "new tab" in result.output.lower() or "opened" in result.output.lower()

    @pytest.mark.asyncio
    async def test_close_tab(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="close_tab"))
        assert result.success
        assert "closed" in result.output.lower()

    @pytest.mark.asyncio
    async def test_switch_tab(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="switch_tab", tab_index=0))
        assert result.success
        assert "switched" in result.output.lower()

    @pytest.mark.asyncio
    async def test_back(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="back"))
        assert result.success

    @pytest.mark.asyncio
    async def test_forward(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="forward"))
        assert result.success

    @pytest.mark.asyncio
    async def test_refresh(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="refresh"))
        assert result.success

    @pytest.mark.asyncio
    async def test_list_tabs(self, _mock_browser):
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="list_tabs"))
        assert result.success

    @pytest.mark.asyncio
    async def test_failure_propagates(self, monkeypatch):
        mock_bc = _make_mock_browser()
        mock_bc.close_tab.return_value = {"success": False, "error": "No browser"}
        monkeypatch.setattr(
            "jarvis.tools.local.browser_tabs._get_browser",
            lambda: mock_bc,
        )
        tool = BrowserTabsTool()
        result = await tool.execute(BrowserTabsArgs(action="close_tab"))
        assert not result.success
        assert "No browser" in result.error
