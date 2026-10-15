"""Tests for jarvis.tools.local.power_control.

CRITICAL: These tests MUST mock subprocess.run and ctypes. A test that
actually calls shutdown would shut down the developer's machine.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.power_control import PowerArgs, PowerControlTool


async def test_shutdown_calls_subprocess():
    with patch("jarvis.tools.local.power_control._run_subprocess") as mock_run:
        mock_run.return_value = ""
        result = await PowerControlTool().execute(
            PowerArgs(action="shutdown", delay_sec=60),
        )
    assert result.success
    mock_run.assert_called_once_with(["shutdown", "/s", "/t", "60"])
    assert "60 seconds" in (result.output or "")


async def test_restart_calls_subprocess():
    with patch("jarvis.tools.local.power_control._run_subprocess") as mock_run:
        mock_run.return_value = ""
        result = await PowerControlTool().execute(
            PowerArgs(action="restart", delay_sec=30),
        )
    assert result.success
    mock_run.assert_called_once_with(["shutdown", "/r", "/t", "30"])


async def test_cancel_shutdown():
    with patch("jarvis.tools.local.power_control._run_subprocess") as mock_run:
        mock_run.return_value = ""
        result = await PowerControlTool().execute(
            PowerArgs(action="cancel_shutdown"),
        )
    assert result.success
    mock_run.assert_called_once_with(["shutdown", "/a"])


async def test_sleep_calls_ctypes():
    mock_powrprof = MagicMock()
    with patch("jarvis.tools.local.power_control.ctypes", create=True) as mock_ctypes:
        mock_ctypes.windll.PowrProf = mock_powrprof
        # Patch the _sleep function to use our mock
        with patch("jarvis.tools.local.power_control._sleep") as mock_sleep:
            mock_sleep.return_value = "Going to sleep."
            result = await PowerControlTool().execute(
                PowerArgs(action="sleep"),
            )
    assert result.success
    assert "sleep" in (result.output or "").lower()


async def test_caffeinate():
    with patch("jarvis.tools.local.power_control._caffeinate") as mock_caf:
        mock_caf.return_value = "Preventing sleep for 60 minutes."
        result = await PowerControlTool().execute(
            PowerArgs(action="caffeinate", caffeinate_minutes=60),
        )
    assert result.success
    assert "60 minutes" in (result.output or "")


async def test_not_implemented_returns_error():
    with patch(
        "jarvis.tools.local.power_control._shutdown",
        side_effect=NotImplementedError("Windows-only"),
    ):
        result = await PowerControlTool().execute(
            PowerArgs(action="shutdown"),
        )
    assert not result.success
    assert "Windows" in (result.error or "")


async def test_unknown_action():
    # Pydantic validates the literal, so this should raise
    with pytest.raises(Exception):
        PowerArgs(action="explode")


def test_requires_confirmation_false():
    """The tool itself does not set requires_confirmation — it is gated
    via TOOL_RISK_MAP = SYSTEM in risk.py."""
    assert PowerControlTool().requires_confirmation is False


def test_delay_bounds():
    with pytest.raises(Exception):
        PowerArgs(action="shutdown", delay_sec=-1)
    with pytest.raises(Exception):
        PowerArgs(action="shutdown", delay_sec=9999)
