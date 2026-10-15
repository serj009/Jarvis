"""Capture a full-page screenshot (not just the visible viewport).

Uses Selenium's Chrome DevTools Protocol to capture the entire page,
including content below the fold.  Falls back to a regular viewport
screenshot via pyautogui/mss if Selenium is not active.

The image is saved to ~/Pictures/Screenshots/ (or ~/Desktop/ as a
fallback) with a timestamped filename.

Dependencies (all optional, checked at runtime):
    - selenium for full-page CDP capture
    - pyautogui or mss for viewport-only fallback
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_save_dir() -> Path:
    """Return the directory to save screenshots into."""
    # Prefer ~/Pictures/Screenshots/
    pictures = Path.home() / "Pictures" / "Screenshots"
    if pictures.parent.exists():
        pictures.mkdir(parents=True, exist_ok=True)
        return pictures
    # Fallback: ~/Desktop/
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop
    # Last resort: home directory.
    return Path.home()


def _generate_filename() -> str:
    """Generate a timestamped filename for the screenshot."""
    return datetime.now().strftime("fullpage_%Y%m%d_%H%M%S.png")


def _capture_full_page_selenium(driver: Any) -> bytes | None:
    """Capture the full page via Chrome DevTools Protocol.

    Returns PNG bytes or None on failure.
    """
    try:
        # Get full page dimensions.
        metrics = driver.execute_cdp_cmd(
            "Page.getLayoutMetrics", {}
        )
        # contentSize gives the full document dimensions.
        content_size = metrics.get("contentSize", {})
        width = content_size.get("width", 1920)
        height = content_size.get("height", 1080)

        # Set device metrics to the full page size.
        driver.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {
                "mobile": False,
                "width": int(width),
                "height": int(height),
                "deviceScaleFactor": 1,
            },
        )

        # Capture the screenshot.
        result = driver.execute_cdp_cmd(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "fromSurface": True,
            },
        )

        # Reset device metrics to defaults.
        driver.execute_cdp_cmd(
            "Emulation.clearDeviceMetricsOverride", {}
        )

        data = result.get("data", "")
        if data:
            return base64.b64decode(data)
        return None
    except Exception as exc:
        log.warning("CDP full-page screenshot failed: %s", exc)
        # Try to reset device metrics even on failure.
        try:
            driver.execute_cdp_cmd(
                "Emulation.clearDeviceMetricsOverride", {}
            )
        except Exception:
            pass
        return None


def _capture_viewport_fallback() -> bytes | None:
    """Capture the visible viewport via pyautogui (fallback).

    Returns PNG bytes or None on failure.
    """
    try:
        import pyautogui
        import io
        image = pyautogui.screenshot()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except ImportError:
        pass

    # Try mss as a second fallback.
    try:
        import mss
        import io
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor.
            shot = sct.grab(monitor)
            # mss returns raw BGRA; convert to PNG via PIL or mss tools.
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
            return png_bytes
    except ImportError:
        pass

    return None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class FullPageScreenshotTool:
    """Capture a full-page screenshot (scrolling beyond the viewport)."""

    name: str = "full_page_screenshot"
    description: str = (
        "Captures a screenshot of the ENTIRE web page, including content "
        "below the visible viewport. Requires Selenium for full-page "
        "capture; falls back to a visible-area screenshot if Selenium is "
        "not active. "
        "(RU: скриншот всей страницы, снимок всей страницы / "
        "UK: скріншот всієї сторінки, знімок всієї сторінки)"
    )
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — 3 patterns
        VoicePattern(
            regex=r"^full\s+page\s+screenshot$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^screenshot\s+(?:the\s+)?entire\s+page$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^capture\s+(?:the\s+)?whole\s+page$",
            priority=18,
            args=lambda m: {},
        ),
        # RU — 3 patterns
        VoicePattern(
            regex=r"^скриншот\s+всей\s+страницы$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^снимок\s+всей\s+страницы$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^скрин\s+целой\s+страницы$",
            priority=18,
            args=lambda m: {},
        ),
        # UK — 3 patterns
        VoicePattern(
            regex=r"^скріншот\s+всієї\s+сторінки$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^знімок\s+всієї\s+сторінки$",
            priority=18,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^скрін\s+цілої\s+сторінки$",
            priority=18,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        browser_controller: Any = None,
    ) -> None:
        self._browser_controller = browser_controller

    async def execute(self, args: EmptyArgs) -> ToolResult:
        """Capture full-page screenshot; fallback to viewport."""
        save_dir = _get_save_dir()
        filename = _generate_filename()
        save_path = save_dir / filename

        # Try full-page via Selenium CDP.
        bc = self._browser_controller
        if bc is not None and getattr(bc, "driver_active", False):
            driver = getattr(bc, "_driver", None)
            if driver is not None:
                png_bytes = await asyncio.to_thread(
                    _capture_full_page_selenium, driver
                )
                if png_bytes:
                    await asyncio.to_thread(
                        self._save_bytes, save_path, png_bytes
                    )
                    log.info("Full-page screenshot saved: %s", save_path)
                    return ToolResult(
                        success=True,
                        output="Full page screenshot saved, sir.",
                    )

        # Fallback: viewport-only.
        png_bytes = await asyncio.to_thread(_capture_viewport_fallback)
        if png_bytes:
            await asyncio.to_thread(
                self._save_bytes, save_path, png_bytes
            )
            log.info("Viewport screenshot saved (fallback): %s", save_path)
            return ToolResult(
                success=True,
                output=(
                    "Screenshot saved (viewport only — Selenium not active "
                    "for full-page capture), sir."
                ),
            )

        return ToolResult(
            success=False,
            error=(
                "Screenshot failed — neither Selenium nor "
                "pyautogui/mss are available."
            ),
        )

    @staticmethod
    def _save_bytes(path: Path, data: bytes) -> None:
        """Write raw bytes to a file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
