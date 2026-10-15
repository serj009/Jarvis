"""Tests for jarvis.tools.local.startup_manager.

All registry access (winreg) is mocked — no system changes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.startup_manager import (
    AddToStartupArgs,
    AddToStartupTool,
    ListStartupTool,
    RemoveFromStartupArgs,
    RemoveFromStartupTool,
)
from jarvis.tools.registry import EmptyArgs


# --- ListStartupTool ------------------------------------------------------

async def test_list_startup_success():
    mock_entries = [
        {
            "name": "Discord",
            "command": r"C:\Users\User\AppData\Local\Discord\Update.exe --processStart Discord.exe",
            "source": r"HKCU\Run",
            "removable": True,
        },
        {
            "name": "SecurityHealth",
            "command": r"%ProgramFiles%\Windows Defender\MSASCuiL.exe",
            "source": r"HKLM\Run",
            "removable": False,
        },
    ]
    with patch(
        "jarvis.tools.local.startup_manager._list_startup_impl",
        return_value=mock_entries,
    ):
        result = await ListStartupTool().execute(EmptyArgs())
    assert result.success
    assert "2 startup" in (result.output or "")
    assert "Discord" in (result.output or "")


async def test_list_startup_empty():
    with patch(
        "jarvis.tools.local.startup_manager._list_startup_impl",
        return_value=[],
    ):
        result = await ListStartupTool().execute(EmptyArgs())
    assert result.success
    assert "no startup" in (result.output or "").lower()


async def test_list_startup_not_implemented():
    with patch(
        "jarvis.tools.local.startup_manager._list_startup_impl",
        side_effect=NotImplementedError("Windows-only"),
    ):
        result = await ListStartupTool().execute(EmptyArgs())
    assert not result.success


# --- RemoveFromStartupTool ------------------------------------------------

async def test_remove_from_startup_success():
    with patch(
        "jarvis.tools.local.startup_manager._remove_from_startup_impl",
        return_value="Removed 'Discord' from startup, sir.",
    ):
        result = await RemoveFromStartupTool().execute(
            RemoveFromStartupArgs(name="Discord"),
        )
    assert result.success
    assert "Discord" in (result.output or "")


async def test_remove_from_startup_not_found():
    with patch(
        "jarvis.tools.local.startup_manager._remove_from_startup_impl",
        return_value="'NonExistent' not found in startup entries, sir.",
    ):
        result = await RemoveFromStartupTool().execute(
            RemoveFromStartupArgs(name="NonExistent"),
        )
    assert result.success
    assert "not found" in (result.output or "")


async def test_remove_from_startup_not_implemented():
    with patch(
        "jarvis.tools.local.startup_manager._remove_from_startup_impl",
        side_effect=NotImplementedError("No winreg"),
    ):
        result = await RemoveFromStartupTool().execute(
            RemoveFromStartupArgs(name="test"),
        )
    assert not result.success


# --- AddToStartupTool -----------------------------------------------------

async def test_add_to_startup_success():
    with patch(
        "jarvis.tools.local.startup_manager._add_to_startup_impl",
        return_value="Added 'MyApp' to startup, sir.",
    ):
        result = await AddToStartupTool().execute(
            AddToStartupArgs(name="MyApp", command=r"C:\MyApp\app.exe"),
        )
    assert result.success
    assert "MyApp" in (result.output or "")


async def test_add_to_startup_not_implemented():
    with patch(
        "jarvis.tools.local.startup_manager._add_to_startup_impl",
        side_effect=NotImplementedError("No winreg"),
    ):
        result = await AddToStartupTool().execute(
            AddToStartupArgs(name="MyApp", command=r"C:\MyApp\app.exe"),
        )
    assert not result.success


def test_requires_confirmation_false():
    """Confirmation is gated via TOOL_RISK_MAP, not requires_confirmation."""
    assert ListStartupTool().requires_confirmation is False
    assert RemoveFromStartupTool().requires_confirmation is False
    assert AddToStartupTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(ListStartupTool.voice_patterns) >= 3
    assert len(RemoveFromStartupTool.voice_patterns) >= 3
    assert len(AddToStartupTool.voice_patterns) >= 3
