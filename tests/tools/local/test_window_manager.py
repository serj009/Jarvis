"""Tests for jarvis.tools.local.window_manager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.window_manager import (
    ListWindowsTool,
    MinimizeAllTool,
    RestoreAllTool,
    SnapLayoutArgs,
    SnapLayoutTool,
    VirtualDesktopArgs,
    VirtualDesktopTool,
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
                {
                    "title": "Chrome",
                    "hwnd": 1,
                    "minimized": False,
                    "maximized": False,
                    "active": True,
                },
                {
                    "title": "Discord",
                    "hwnd": 2,
                    "minimized": True,
                    "maximized": False,
                    "active": False,
                },
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


# --- corner / center snap (features 1 & 2) ---------------------------------

@pytest.mark.parametrize("action", [
    "snap_top_left", "snap_top_right",
    "snap_bottom_left", "snap_bottom_right", "snap_center",
])
async def test_corner_and_center_snap(action):
    win = _fake_window("Chrome")
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw:
        mock_gw.return_value = MagicMock()
        mock_gw.return_value.getAllWindows.return_value = [win]
        result = await WindowActionTool().execute(
            WindowActionArgs(action=action, window="chrome"),
        )
    assert result.success
    win.restore.assert_called()
    win.moveTo.assert_called_once()
    win.resizeTo.assert_called_once()


def test_snap_geometry_table_covers_all_snaps():
    geo = WindowActionTool._SNAP_GEOMETRY
    for key in (
        "snap_left", "snap_right", "snap_top_left", "snap_top_right",
        "snap_bottom_left", "snap_bottom_right", "snap_center",
    ):
        assert key in geo
    # center is a 50%×50% box offset to the middle
    assert geo["snap_center"] == (0.25, 0.25, 0.5, 0.5)


# --- SnapLayoutTool (feature 3) --------------------------------------------

async def test_snap_layout_places_two_windows():
    left = _fake_window("Visual Studio Code")
    right = _fake_window("Google Chrome")

    def fake_find(title):
        t = title.lower()
        if "code" in t or "vs" in t:
            return left
        if "chrome" in t:
            return right
        return None

    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw, \
         patch("jarvis.tools.local.window_manager._find_window", side_effect=fake_find):
        mock_gw.return_value = MagicMock()
        result = await SnapLayoutTool().execute(
            SnapLayoutArgs(left="vs code", right="chrome"),
        )
    assert result.success
    left.moveTo.assert_called_once()
    right.moveTo.assert_called_once()


async def test_snap_layout_missing_window_fails():
    with patch("jarvis.tools.local.window_manager._get_pygetwindow") as mock_gw, \
         patch("jarvis.tools.local.window_manager._find_window", return_value=None):
        mock_gw.return_value = MagicMock()
        result = await SnapLayoutTool().execute(
            SnapLayoutArgs(left="ghost", right="chrome"),
        )
    assert not result.success
    assert "not found" in (result.error or "").lower()


# --- MinimizeAll / RestoreAll (feature 4) ----------------------------------

async def test_minimize_all_sends_win_d():
    with patch("jarvis.tools.local.window_manager._send_keys", return_value=True) as sk:
        result = await MinimizeAllTool().execute(MinimizeAllTool.args_schema())
    assert result.success
    sk.assert_called_once_with("win", "d")


async def test_restore_all_sends_win_d():
    with patch("jarvis.tools.local.window_manager._send_keys", return_value=True) as sk:
        result = await RestoreAllTool().execute(RestoreAllTool.args_schema())
    assert result.success
    sk.assert_called_once_with("win", "d")


async def test_minimize_all_without_pyautogui_fails():
    with patch("jarvis.tools.local.window_manager._send_keys", return_value=False):
        result = await MinimizeAllTool().execute(MinimizeAllTool.args_schema())
    assert not result.success


# --- VirtualDesktopTool (feature 5) ----------------------------------------

@pytest.mark.parametrize("action,keys", [
    ("next", ("win", "ctrl", "right")),
    ("previous", ("win", "ctrl", "left")),
    ("task_view", ("win", "tab")),
])
async def test_virtual_desktop_actions(action, keys):
    with patch("jarvis.tools.local.window_manager._send_keys", return_value=True) as sk:
        result = await VirtualDesktopTool().execute(
            VirtualDesktopArgs(action=action),
        )
    assert result.success
    sk.assert_called_once_with(*keys)


# --- voice patterns (feature 6) --------------------------------------------

def _match(tool_cls, utterance):
    """Mimic the router: first-match-wins by ascending priority."""
    import re
    for vp in sorted(tool_cls.voice_patterns, key=lambda p: p.priority):
        m = re.match(vp.regex, utterance)
        if m:
            return vp.args(m)
    return None


@pytest.mark.parametrize("utterance,expected_action", [
    ("snap chrome top left", "snap_top_left"),
    ("snap chrome bottom right", "snap_bottom_right"),
    ("center chrome", "snap_center"),
    ("snap discord to the center", "snap_center"),
    ("snap chrome left", "snap_left"),
    ("snap chrome right", "snap_right"),
])
def test_en_snap_patterns_route(utterance, expected_action):
    args = _match(WindowActionTool, utterance)
    assert args is not None, f"no pattern matched {utterance!r}"
    assert args["action"] == expected_action


@pytest.mark.parametrize("utterance,expected_action", [
    ("помести хром в верхний левый угол", "snap_top_left"),
    ("поставь хром слева", "snap_left"),
    ("центрируй дискорд", "snap_center"),
])
def test_ru_snap_patterns_route(utterance, expected_action):
    args = _match(WindowActionTool, utterance)
    assert args is not None, f"no pattern matched {utterance!r}"
    assert args["action"] == expected_action


def test_snap_layout_pattern_routes():
    assert _match(SnapLayoutTool, "vs code left, chrome right") == {
        "left": "vs code", "right": "chrome",
    }
    assert _match(SnapLayoutTool, "vs code слева, chrome справа") == {
        "left": "vs code", "right": "chrome",
    }


def test_minimize_all_patterns_route():
    assert _match(MinimizeAllTool, "minimize all") == {}
    assert _match(MinimizeAllTool, "show desktop") == {}
    assert _match(MinimizeAllTool, "сверни всё") == {}


def test_virtual_desktop_patterns_route():
    assert _match(VirtualDesktopTool, "next desktop") == {"action": "next"}
    assert _match(VirtualDesktopTool, "previous desktop") == {"action": "previous"}
    assert _match(VirtualDesktopTool, "task view") == {"action": "task_view"}
    assert _match(VirtualDesktopTool, "следующий рабочий стол") == {"action": "next"}


def test_new_tools_require_no_confirmation():
    for tool in (SnapLayoutTool(), MinimizeAllTool(), RestoreAllTool(), VirtualDesktopTool()):
        assert tool.requires_confirmation is False
