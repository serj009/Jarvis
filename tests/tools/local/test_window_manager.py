"""Tests for jarvis.tools.local.window_manager."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.window_manager import (
    ListWindowsTool,
    WindowActionArgs,
    WindowActionTool,
)
from jarvis.tools.registry import EmptyArgs


# --- helpers ---------------------------------------------------------------

def _fake_window(title="Chrome", visible=True, minimized=False, maximized=False, active=False):
    w = MagicMock()
    w.title = title
    w.visible = visible
    w.isMinimized = minimized
    w.isMaximized = maximized
    w.isActive = active
    w._hWnd = 12345
    w.activate = MagicMock()
    w.minimize = MagicMock()
    w.maximize = MagicMock()
    w.restore = MagicMock()
    w.close = MagicMock()
    w.moveTo = MagicMock()
    w.resizeTo = MagicMock()
    return w


# --- WindowActionTool ------------------------------------------------------

async def test_focus_window():
    win = _fake_window("Google Chrome")
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw:
        mock_gw.return_value = MagicMock()
        mock_gw.return_value.getAllWindows.return_value = [win]
        result = await WindowActionTool().execute(
            WindowActionArgs(action="focus", window="chrome"),
        )
    assert result.success
    win.activate.assert_called_once()


async def test_minimize_window():
    win = _fake_window("Discord")
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw:
        mock_gw.return_value = MagicMock()
        mock_gw.return_value.getAllWindows.return_value = [win]
        result = await WindowActionTool().execute(
            WindowActionArgs(action="minimize", window="discord"),
        )
    assert result.success
    win.minimize.assert_called_once()


async def test_window_not_found():
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw:
        mock_gw.return_value = MagicMock()
        mock_gw.return_value.getAllWindows.return_value = []
        result = await WindowActionTool().execute(
            WindowActionArgs(action="focus", window="nonexistent"),
        )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_pygetwindow_not_installed():
    with patch("jarvis.tools.local.window_manager._get_pygetwindow", return_value=None):
        result = await WindowActionTool().execute(
            WindowActionArgs(action="focus", window="chrome"),
        )
    assert not result.success
    assert "pygetwindow" in (result.error or "").lower()


# --- ListWindowsTool -------------------------------------------------------

async def test_list_windows():
    win1 = _fake_window("Chrome", active=True)
    win2 = _fake_window("Discord", minimized=True)
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw:
        mock_gw.return_value = MagicMock()
        mock_gw.return_value.getAllWindows.return_value = [win1, win2]
        # Patch _list_windows_impl to use pygetwindow path
        with patch("jarvis.tools.local.window_manager._list_windows_impl") as mock_list:
            mock_list.return_value = [
                {"title": "Chrome", "hwnd": 1, "minimized": False, "maximized": False, "active": True},
                {"title": "Discord", "hwnd": 2, "minimized": True, "maximized": False, "active": False},
            ]
            result = await ListWindowsTool().execute(EmptyArgs())
    assert result.success
    assert "2 window" in (result.output or "")


async def test_list_windows_empty():
    with patch("jarvis.tools.local.window_manager._list_windows_impl", return_value=[]):
        result = await ListWindowsTool().execute(EmptyArgs())
    assert result.success
    assert "no visible" in (result.output or "").lower()


def test_requires_confirmation_false():
    assert WindowActionTool().requires_confirmation is False
    assert ListWindowsTool().requires_confirmation is False
