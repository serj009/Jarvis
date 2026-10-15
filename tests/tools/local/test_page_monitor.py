"""Tests for jarvis.tools.local.page_monitor.

Fully mocked: no real network, no real filesystem.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.page_monitor import (
    CheckPageArgs,
    CheckPageChangesTool,
    ListPageMonitorsTool,
    MonitorStore,
    StartMonitorArgs,
    StartPageMonitorTool,
    StopMonitorArgs,
    StopPageMonitorTool,
    _compute_diff,
    _text_hash,
)
from jarvis.tools.registry import EmptyArgs


FAKE_PAGE_V1 = "Hello World\nThis is version 1.\nPrice: $100"
FAKE_PAGE_V2 = "Hello World\nThis is version 2.\nPrice: $200\nNew section added."


# ---------------------------------------------------------------------------
# Unit tests — MonitorStore
# ---------------------------------------------------------------------------

@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_start_monitoring(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    result = store.start_monitoring("https://example.com", interval_minutes=10)
    assert result["success"]
    assert result["url"] == "https://example.com"
    assert result["snapshot_chars"] == len(FAKE_PAGE_V1)


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_start_duplicate(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    result = store.start_monitoring("https://example.com")
    assert not result["success"]
    assert "Already" in result["error"]


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_start_fetch_error(mock_fetch):
    mock_fetch.return_value = ("", "No internet connection.")
    store = MonitorStore()
    result = store.start_monitoring("https://example.com")
    assert not result["success"]
    assert "No internet" in result["error"]


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_stop_monitoring(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    result = store.stop_monitoring("https://example.com")
    assert result["success"]


def test_stop_nonexistent():
    store = MonitorStore()
    result = store.stop_monitoring("https://nobody.com")
    assert not result["success"]


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_list_monitors(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    assert store.list_monitors() == []
    store.start_monitoring("https://example.com", interval_minutes=15)
    monitors = store.list_monitors()
    assert len(monitors) == 1
    assert monitors[0]["url"] == "https://example.com"
    assert monitors[0]["interval_minutes"] == 15


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_check_no_change(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    result = store.check_now("https://example.com")
    assert result["success"]
    assert result["changed"] is False


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_check_with_change(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")

    # Simulate page change.
    mock_fetch.return_value = (FAKE_PAGE_V2, None)
    result = store.check_now("https://example.com")
    assert result["success"]
    assert result["changed"] is True
    assert "diff" in result
    assert "version 2" in result["diff"] or "$200" in result["diff"]


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
def test_check_triggers_callback(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    callback = MagicMock()
    store = MonitorStore(on_change=callback)
    store.start_monitoring("https://example.com")

    mock_fetch.return_value = (FAKE_PAGE_V2, None)
    store.check_now("https://example.com")
    callback.assert_called_once()
    assert callback.call_args[0][0] == "https://example.com"


def test_check_not_monitored():
    store = MonitorStore()
    result = store.check_now("https://nobody.com")
    assert not result["success"]


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------

def test_compute_diff_no_change():
    assert _compute_diff("same", "same") == ""


def test_compute_diff_shows_changes():
    diff = _compute_diff("line 1\nold line\nline 3", "line 1\nnew line\nline 3")
    assert "old line" in diff or "new line" in diff


def test_text_hash_deterministic():
    assert _text_hash("hello") == _text_hash("hello")
    assert _text_hash("hello") != _text_hash("world")


# ---------------------------------------------------------------------------
# Tool-level async tests
# ---------------------------------------------------------------------------

@patch("jarvis.tools.local.page_monitor._fetch_page_text")
async def test_start_monitor_tool(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    tool = StartPageMonitorTool(store=store)
    result = await tool.execute(StartMonitorArgs(url="https://example.com", interval_minutes=5))
    assert result.success
    assert "monitoring" in (result.output or "").lower()


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
async def test_stop_monitor_tool(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    tool = StopPageMonitorTool(store=store)
    result = await tool.execute(StopMonitorArgs(url="https://example.com"))
    assert result.success


async def test_stop_monitor_tool_not_found():
    store = MonitorStore()
    tool = StopPageMonitorTool(store=store)
    result = await tool.execute(StopMonitorArgs(url="https://nobody.com"))
    assert not result.success


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
async def test_check_changes_tool_no_change(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    tool = CheckPageChangesTool(store=store)
    result = await tool.execute(CheckPageArgs(url="https://example.com"))
    assert result.success
    assert "no changes" in (result.output or "").lower()


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
async def test_check_changes_tool_with_change(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")

    mock_fetch.return_value = (FAKE_PAGE_V2, None)
    tool = CheckPageChangesTool(store=store)
    result = await tool.execute(CheckPageArgs(url="https://example.com"))
    assert result.success
    assert "changes detected" in (result.output or "").lower()


async def test_list_monitors_tool_empty():
    store = MonitorStore()
    tool = ListPageMonitorsTool(store=store)
    result = await tool.execute(EmptyArgs())
    assert result.success
    assert "no pages" in (result.output or "").lower()


@patch("jarvis.tools.local.page_monitor._fetch_page_text")
async def test_list_monitors_tool_with_monitors(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_V1, None)
    store = MonitorStore()
    store.start_monitoring("https://example.com")
    tool = ListPageMonitorsTool(store=store)
    result = await tool.execute(EmptyArgs())
    assert result.success
    assert "example.com" in (result.output or "")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_requires_confirmation_false():
    assert StartPageMonitorTool().requires_confirmation is False
    assert StopPageMonitorTool().requires_confirmation is False
    assert CheckPageChangesTool().requires_confirmation is False
    assert ListPageMonitorsTool().requires_confirmation is False
