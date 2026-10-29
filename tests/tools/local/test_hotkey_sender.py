"""Tests for jarvis.tools.local.hotkey_sender.

All pyautogui calls are mocked — no real keystrokes sent.
"""

from __future__ import annotations

from unittest.mock import patch


from jarvis.tools.local.hotkey_sender import (
    SendHotkeyArgs,
    SendHotkeyTool,
    _is_dangerous,
    _parse_hotkey,
)

# --- _parse_hotkey --------------------------------------------------------

def test_parse_ctrl_c():
    assert _parse_hotkey("ctrl+c") == ["ctrl", "c"]


def test_parse_alt_tab():
    assert _parse_hotkey("alt+tab") == ["alt", "tab"]


def test_parse_win_d():
    assert _parse_hotkey("win+d") == ["win", "d"]


def test_parse_ctrl_shift_t():
    assert _parse_hotkey("ctrl+shift+t") == ["ctrl", "shift", "t"]


def test_parse_aliases():
    assert _parse_hotkey("control+c") == ["ctrl", "c"]
    assert _parse_hotkey("windows+d") == ["win", "d"]
    assert _parse_hotkey("esc") == ["escape"]


def test_parse_spaces():
    """Spaces are stripped during parsing."""
    assert _parse_hotkey("ctrl + c") == ["ctrl", "c"]


# --- _is_dangerous --------------------------------------------------------

def test_dangerous_alt_f4():
    assert _is_dangerous("alt+f4") is True


def test_dangerous_ctrl_w():
    assert _is_dangerous("ctrl+w") is True


def test_safe_ctrl_c():
    assert _is_dangerous("ctrl+c") is False


def test_safe_alt_tab():
    assert _is_dangerous("alt+tab") is False


# --- SendHotkeyTool -------------------------------------------------------

async def test_send_hotkey_success():
    with patch(
        "jarvis.tools.local.hotkey_sender._send_hotkey_impl",
        return_value="Pressed ctrl+c, sir.",
    ):
        result = await SendHotkeyTool().execute(
            SendHotkeyArgs(combo="ctrl+c"),
        )
    assert result.success
    assert "ctrl+c" in (result.output or "")


async def test_send_hotkey_dangerous_blocked():
    """Dangerous combinations should be blocked with an error."""
    result = await SendHotkeyTool().execute(
        SendHotkeyArgs(combo="alt+f4"),
    )
    assert not result.success
    assert "dangerous" in (result.error or "").lower()


async def test_send_hotkey_not_implemented():
    with patch(
        "jarvis.tools.local.hotkey_sender._send_hotkey_impl",
        side_effect=NotImplementedError("pyautogui required"),
    ):
        result = await SendHotkeyTool().execute(
            SendHotkeyArgs(combo="ctrl+v"),
        )
    assert not result.success
    assert "pyautogui" in (result.error or "").lower()


async def test_send_hotkey_runtime_error():
    with patch(
        "jarvis.tools.local.hotkey_sender._send_hotkey_impl",
        side_effect=RuntimeError("Key not found"),
    ):
        result = await SendHotkeyTool().execute(
            SendHotkeyArgs(combo="ctrl+v"),
        )
    assert not result.success


async def test_send_hotkey_empty():
    # An empty combo after parsing should fail
    result = await SendHotkeyTool().execute(
        SendHotkeyArgs(combo=""),
    )
    assert not result.success


def test_requires_confirmation_false():
    """Confirmation is handled dynamically based on the combo."""
    assert SendHotkeyTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(SendHotkeyTool.voice_patterns) >= 6


def test_confirmation_summary():
    tool = SendHotkeyTool()
    summary = tool.confirmation_summary(SendHotkeyArgs(combo="ctrl+c"))
    assert "ctrl+c" in summary
