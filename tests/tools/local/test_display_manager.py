"""Tests for jarvis.tools.local.display_manager.

All hardware access (WMI, ctypes) is mocked — no monitor needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.tools.local.display_manager import (
    DisplayInfoTool,
    SetBrightnessArgs,
    SetBrightnessTool,
    ToggleNightModeArgs,
    ToggleNightModeTool,
)
from jarvis.tools.registry import EmptyArgs

# --- SetBrightnessTool ----------------------------------------------------

async def test_set_brightness_success():
    with patch(
        "jarvis.tools.local.display_manager._set_brightness_impl",
        return_value="Brightness set to 75%, sir.",
    ):
        result = await SetBrightnessTool().execute(
            SetBrightnessArgs(level=75),
        )
    assert result.success
    assert "75%" in (result.output or "")


async def test_set_brightness_not_implemented():
    with patch(
        "jarvis.tools.local.display_manager._set_brightness_impl",
        side_effect=NotImplementedError("No WMI"),
    ):
        result = await SetBrightnessTool().execute(
            SetBrightnessArgs(level=50),
        )
    assert not result.success
    assert "WMI" in (result.error or "")


async def test_set_brightness_generic_error():
    with patch(
        "jarvis.tools.local.display_manager._set_brightness_impl",
        side_effect=RuntimeError("unknown"),
    ):
        result = await SetBrightnessTool().execute(
            SetBrightnessArgs(level=50),
        )
    assert not result.success


def test_brightness_bounds():
    with pytest.raises(Exception):
        SetBrightnessArgs(level=-1)
    with pytest.raises(Exception):
        SetBrightnessArgs(level=101)


# --- ToggleNightModeTool --------------------------------------------------

async def test_night_mode_enable():
    with patch(
        "jarvis.tools.local.display_manager._toggle_night_mode_impl",
        return_value="Night mode enabled, sir.",
    ):
        result = await ToggleNightModeTool().execute(
            ToggleNightModeArgs(enable=True),
        )
    assert result.success
    assert "enabled" in (result.output or "")


async def test_night_mode_disable():
    with patch(
        "jarvis.tools.local.display_manager._toggle_night_mode_impl",
        return_value="Night mode disabled, sir.",
    ):
        result = await ToggleNightModeTool().execute(
            ToggleNightModeArgs(enable=False),
        )
    assert result.success
    assert "disabled" in (result.output or "")


async def test_night_mode_not_implemented():
    with patch(
        "jarvis.tools.local.display_manager._toggle_night_mode_impl",
        side_effect=NotImplementedError("No registry key"),
    ):
        result = await ToggleNightModeTool().execute(
            ToggleNightModeArgs(enable=True),
        )
    assert not result.success


# --- DisplayInfoTool ------------------------------------------------------

async def test_display_info_success():
    mock_info = {
        "monitor_count": 2,
        "monitors": [
            {
                "name": r"\\.\DISPLAY1",
                "adapter": "NVIDIA GeForce",
                "width": 1920,
                "height": 1080,
                "refresh_hz": 60,
                "bits_per_pixel": 32,
            },
            {
                "name": r"\\.\DISPLAY2",
                "adapter": "NVIDIA GeForce",
                "width": 2560,
                "height": 1440,
                "refresh_hz": 144,
                "bits_per_pixel": 32,
            },
        ],
        "brightness": 80,
    }
    with patch(
        "jarvis.tools.local.display_manager._display_info_impl",
        return_value=mock_info,
    ):
        result = await DisplayInfoTool().execute(EmptyArgs())
    assert result.success
    assert "2 monitor" in (result.output or "")
    assert "1920x1080" in (result.output or "")
    assert "2560x1440" in (result.output or "")
    assert "80%" in (result.output or "")


async def test_display_info_no_monitors():
    with patch(
        "jarvis.tools.local.display_manager._display_info_impl",
        return_value={"monitor_count": 0, "monitors": [], "brightness": None},
    ):
        result = await DisplayInfoTool().execute(EmptyArgs())
    assert result.success
    assert "no display" in (result.output or "").lower()


def test_requires_confirmation_false():
    assert SetBrightnessTool().requires_confirmation is False
    assert ToggleNightModeTool().requires_confirmation is False
    assert DisplayInfoTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(SetBrightnessTool.voice_patterns) >= 3
    assert len(ToggleNightModeTool.voice_patterns) >= 3
    assert len(DisplayInfoTool.voice_patterns) >= 3
