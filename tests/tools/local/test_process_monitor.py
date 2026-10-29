"""Tests for jarvis.tools.local.process_monitor.

All tests mock psutil and subprocess to avoid inspecting real processes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from jarvis.tools.local.process_monitor import (
    HeavyProcessesArgs,
    HeavyProcessesTool,
    ProcessInfoArgs,
    ProcessInfoTool,
    WaitForExitArgs,
    WaitForProcessTool,
)

# --- ProcessInfoTool -------------------------------------------------------

async def test_process_info_found():
    with patch("jarvis.tools.local.process_monitor._get_process_info") as mock_info:
        mock_info.return_value = {
            "success": True,
            "name": "chrome",
            "process_count": 12,
            "total_memory_mb": 1500.0,
            "total_cpu_percent": 15.0,
            "processes": [],
        }
        result = await ProcessInfoTool().execute(
            ProcessInfoArgs(name="chrome"),
        )
    assert result.success
    assert "12 process" in (result.output or "")
    assert "1500" in (result.output or "")


async def test_process_info_not_found():
    with patch("jarvis.tools.local.process_monitor._get_process_info") as mock_info:
        mock_info.return_value = {
            "success": False,
            "error": "Process 'nonexistent' not found.",
        }
        result = await ProcessInfoTool().execute(
            ProcessInfoArgs(name="nonexistent"),
        )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_process_info_exception():
    with patch(
        "jarvis.tools.local.process_monitor._get_process_info",
        side_effect=RuntimeError("psutil error"),
    ):
        result = await ProcessInfoTool().execute(
            ProcessInfoArgs(name="chrome"),
        )
    assert not result.success
    assert "failed" in (result.error or "").lower()


# --- HeavyProcessesTool ----------------------------------------------------

async def test_heavy_processes():
    with patch("jarvis.tools.local.process_monitor._list_heavy_processes") as mock_heavy:
        mock_heavy.return_value = {
            "success": True,
            "processes": [
                {"name": "chrome.exe", "pid": 123, "memory_mb": 1500.0},
                {"name": "discord.exe", "pid": 456, "memory_mb": 800.0},
            ],
            "total_count": 100,
        }
        result = await HeavyProcessesTool().execute(
            HeavyProcessesArgs(top_n=10),
        )
    assert result.success
    assert "chrome" in (result.output or "").lower()
    assert "discord" in (result.output or "").lower()


async def test_heavy_processes_psutil_missing():
    with patch("jarvis.tools.local.process_monitor._list_heavy_processes") as mock_heavy:
        mock_heavy.return_value = {
            "success": False,
            "error": "psutil not installed.",
        }
        result = await HeavyProcessesTool().execute(
            HeavyProcessesArgs(top_n=5),
        )
    assert not result.success
    assert "psutil" in (result.error or "").lower()


# --- WaitForProcessTool ---------------------------------------------------

async def test_wait_process_exits():
    with patch("jarvis.tools.local.process_monitor._wait_for_exit") as mock_wait:
        mock_wait.return_value = {
            "success": True,
            "message": "Process 'setup.exe' has exited.",
        }
        result = await WaitForProcessTool().execute(
            WaitForExitArgs(name="setup.exe", timeout=60),
        )
    assert result.success
    assert "finished" in (result.output or "").lower()


async def test_wait_process_timeout():
    with patch("jarvis.tools.local.process_monitor._wait_for_exit") as mock_wait:
        mock_wait.return_value = {
            "success": False,
            "error": "Timeout: 'setup.exe' still running after 10s.",
        }
        result = await WaitForProcessTool().execute(
            WaitForExitArgs(name="setup.exe", timeout=10),
        )
    assert not result.success
    assert "timeout" in (result.error or "").lower()


def test_requires_confirmation_false():
    assert ProcessInfoTool().requires_confirmation is False
    assert HeavyProcessesTool().requires_confirmation is False
    assert WaitForProcessTool().requires_confirmation is False


def test_top_n_bounds():
    with pytest.raises(ValidationError):
        HeavyProcessesArgs(top_n=0)
    with pytest.raises(ValidationError):
        HeavyProcessesArgs(top_n=100)


def test_wait_timeout_bounds():
    with pytest.raises(ValidationError):
        WaitForExitArgs(name="test", timeout=5)
    with pytest.raises(ValidationError):
        WaitForExitArgs(name="test", timeout=9999)
