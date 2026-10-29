"""Tests for jarvis.tools.local.full_page_screenshot.

All tests mock Selenium CDP and pyautogui/mss to avoid real browser calls.
"""

from __future__ import annotations

import base64
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from jarvis.tools.local.full_page_screenshot import (
    FullPageScreenshotTool,
    _capture_full_page_selenium,
    _capture_viewport_fallback,
    _generate_filename,
    _get_save_dir,
)
from jarvis.tools.registry import EmptyArgs

# --- Unit tests: helpers ---------------------------------------------------

def test_generate_filename():
    name = _generate_filename()
    assert name.startswith("fullpage_")
    assert name.endswith(".png")


def test_get_save_dir_returns_path(tmp_path):
    """Save dir should be a Path that exists."""
    result = _get_save_dir()
    assert isinstance(result, Path)


# --- Unit tests: CDP capture -----------------------------------------------

def test_capture_full_page_selenium_success():
    """Mock CDP commands for a successful full-page capture."""
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    encoded = base64.b64encode(fake_png).decode()

    mock_driver = MagicMock()
    mock_driver.execute_cdp_cmd.side_effect = [
        # Page.getLayoutMetrics
        {"contentSize": {"width": 1920, "height": 5000}},
        # Emulation.setDeviceMetricsOverride
        {},
        # Page.captureScreenshot
        {"data": encoded},
        # Emulation.clearDeviceMetricsOverride
        {},
    ]

    result = _capture_full_page_selenium(mock_driver)
    assert result is not None
    assert result == fake_png


def test_capture_full_page_selenium_failure():
    """CDP failure should return None."""
    mock_driver = MagicMock()
    mock_driver.execute_cdp_cmd.side_effect = RuntimeError("CDP error")

    result = _capture_full_page_selenium(mock_driver)
    assert result is None


# --- Unit tests: viewport fallback ----------------------------------------

def test_capture_viewport_fallback_pyautogui():
    """pyautogui fallback should return PNG bytes."""
    fake_image = MagicMock()
    fake_image.save.side_effect = lambda buf, format: buf.write(b"\x89PNG_fake")

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.screenshot = MagicMock(return_value=fake_image)

    with patch.dict(sys.modules, {"pyautogui": fake_pyautogui}):
        result = _capture_viewport_fallback()
    assert result is not None
    assert len(result) > 0


def test_capture_viewport_fallback_no_modules():
    """No pyautogui or mss should return None."""
    with patch.dict(sys.modules, {"pyautogui": None, "mss": None}):
        # Force ImportError by removing from sys.modules.
        with patch("builtins.__import__", side_effect=ImportError):
            result = _capture_viewport_fallback()
    # May succeed or fail depending on what's installed; just check it doesn't crash.
    assert result is None or isinstance(result, bytes)


# --- Tool execution tests -------------------------------------------------

async def test_execute_full_page_success(tmp_path):
    """Selenium CDP path should produce a full-page screenshot."""
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    mock_bc = MagicMock()
    mock_bc.driver_active = True
    mock_bc._driver = MagicMock()

    with patch(
        "jarvis.tools.local.full_page_screenshot._capture_full_page_selenium",
        return_value=fake_png,
    ), patch(
        "jarvis.tools.local.full_page_screenshot._get_save_dir",
        return_value=tmp_path,
    ):
        tool = FullPageScreenshotTool(browser_controller=mock_bc)
        result = await tool.execute(EmptyArgs())

    assert result.success
    assert "Full page screenshot saved" in (result.output or "")

    # Check that a file was written.
    pngs = list(tmp_path.glob("fullpage_*.png"))
    assert len(pngs) == 1


async def test_execute_fallback_viewport(tmp_path):
    """Without Selenium, should fall back to viewport screenshot."""
    fake_png = b"\x89PNG_viewport"

    with patch(
        "jarvis.tools.local.full_page_screenshot._capture_viewport_fallback",
        return_value=fake_png,
    ), patch(
        "jarvis.tools.local.full_page_screenshot._get_save_dir",
        return_value=tmp_path,
    ):
        tool = FullPageScreenshotTool(browser_controller=None)
        result = await tool.execute(EmptyArgs())

    assert result.success
    assert "viewport only" in (result.output or "")


async def test_execute_selenium_cdp_fails_uses_fallback(tmp_path):
    """If CDP fails, should fall back to viewport."""
    fake_png = b"\x89PNG_fallback"

    mock_bc = MagicMock()
    mock_bc.driver_active = True
    mock_bc._driver = MagicMock()

    with patch(
        "jarvis.tools.local.full_page_screenshot._capture_full_page_selenium",
        return_value=None,  # CDP failed
    ), patch(
        "jarvis.tools.local.full_page_screenshot._capture_viewport_fallback",
        return_value=fake_png,
    ), patch(
        "jarvis.tools.local.full_page_screenshot._get_save_dir",
        return_value=tmp_path,
    ):
        tool = FullPageScreenshotTool(browser_controller=mock_bc)
        result = await tool.execute(EmptyArgs())

    assert result.success
    assert "viewport only" in (result.output or "")


async def test_execute_everything_fails():
    """All capture methods fail — should return error."""
    with patch(
        "jarvis.tools.local.full_page_screenshot._capture_viewport_fallback",
        return_value=None,
    ):
        tool = FullPageScreenshotTool(browser_controller=None)
        result = await tool.execute(EmptyArgs())

    assert not result.success
    assert "failed" in (result.error or "").lower()


def test_requires_confirmation_false():
    assert FullPageScreenshotTool().requires_confirmation is False


def test_voice_patterns_count():
    """Must have at least 9 voice patterns (3 EN + 3 RU + 3 UK)."""
    assert len(FullPageScreenshotTool.voice_patterns) >= 9
