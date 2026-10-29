"""Quick Share / QR Code — generate QR codes for URLs and text.

Generates QR codes for easy phone scanning. Supports:
- generate_qr: create a QR code PNG for any text/URL
- share_to_phone: get the current page URL as a QR code

QR generation via ``qrcode`` library (lazy import) with fallback
to an online API if the library is not installed.
QR images are saved as PNG in ``~/.jarvis/data/qr_codes/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def _qr_dir() -> Path:
    # Single data-dir resolver (jarvis.paths); layout unchanged:
    # <data_dir>/data/qr_codes. Resolved at call time.
    from jarvis.paths import data_path

    return data_path("data", "qr_codes")


# ---------------------------------------------------------------------------
# QR generation helpers
# ---------------------------------------------------------------------------

def _generate_qr_image(data: str, out_path: Path) -> str | None:
    """Generate QR code PNG. Returns error string or None on success."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Try qrcode library first
    try:
        import qrcode
        img = qrcode.make(data)
        img.save(str(out_path))
        return None
    except ImportError:
        log.debug("qrcode library not installed, trying fallback")
    except Exception as exc:
        log.warning("qrcode library failed: %s", exc)

    # Fallback: use httpx to fetch from a public QR API
    try:
        import httpx
        api_url = (
            f"https://api.qrserver.com/v1/create-qr-code/"
            f"?size=300x300&data={httpx.URL(data)}"
        )
        resp = httpx.get(api_url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        return None
    except ImportError:
        return "Neither qrcode nor httpx is installed."
    except Exception as exc:
        return f"QR generation failed: {exc}"


def _open_image(path: Path) -> None:
    """Open an image with the system default viewer."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.debug("Could not open QR image", exc_info=True)


def _get_current_url() -> str | None:
    """Try to get the URL from the active Selenium browser tab."""
    try:
        from jarvis.tools.local.browser_tabs import _get_browser
        bc = _get_browser()
        if getattr(bc, "driver_active", False):
            return bc.driver.current_url
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class GenerateQrArgs(BaseModel):
    data: str = Field(
        description="Text or URL to encode into a QR code.",
    )
    show: bool = Field(
        default=True,
        description="Whether to open the QR image in the default viewer.",
    )


class ShareToPhoneArgs(BaseModel):
    """Generates a QR code for the current browser page URL."""
    pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class GenerateQrTool:
    name: str = "generate_qr"
    description: str = (
        "Generates a QR code image (PNG) for the given text or URL. "
        "Opens the image for easy phone scanning. "
        "(RU: покажи qr код, сгенерируй qr / "
        "UK: покажи qr код, згенеруй qr)"
    )
    args_schema = GenerateQrArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:show|generate|create|make)\s+(?:a\s+)?qr\s+(?:code\s+)?(?:for\s+)?(.+)$",
            priority=454,
            args=lambda m: {"data": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^qr\s+(?:code\s+)?(?:for\s+)?(.+)$",
            priority=454,
            args=lambda m: {"data": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:покажи|сгенерируй|создай)\s+qr\s+(?:код\s+)?(?:для\s+)?(.+)?$",
            priority=454,
            args=lambda m: {"data": (m.group(1) or "").strip()},
        ),
        VoicePattern(
            regex=r"^qr\s+(?:код\s+)?(?:для\s+)?(?:этой\s+)?страниц[ыу]$",
            priority=454,
            args=lambda m: {"data": ""},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:покажи|згенеруй|створи)\s+qr\s+(?:код\s+)?(?:для\s+)?(.+)?$",
            priority=454,
            args=lambda m: {"data": (m.group(1) or "").strip()},
        ),
        VoicePattern(
            regex=r"^qr\s+(?:код\s+)?(?:для\s+)?(?:цієї\s+)?сторінк[иу]$",
            priority=454,
            args=lambda m: {"data": ""},
        ),
    )

    async def execute(self, args: GenerateQrArgs) -> ToolResult:
        data = args.data.strip()
        if not data:
            # Try to get URL from browser
            url = await asyncio.to_thread(_get_current_url)
            if url:
                data = url
            else:
                return ToolResult(
                    success=False,
                    error="No text/URL provided and no active browser tab.",
                )

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = _qr_dir() / f"qr_{timestamp}.png"

        err = await asyncio.to_thread(_generate_qr_image, data, out_path)
        if err:
            return ToolResult(success=False, error=err)

        if args.show:
            await asyncio.to_thread(_open_image, out_path)

        return ToolResult(
            success=True,
            output=f"QR code saved: {out_path}\nEncoded: {data[:100]}",
        )


class ShareToPhoneTool:
    name: str = "share_to_phone"
    description: str = (
        "Shows a QR code of the current browser page URL so you can "
        "quickly open it on your phone. Requires Selenium. "
        "(RU: открой ссылку на телефоне / "
        "UK: відкрий посилання на телефоні)"
    )
    args_schema = ShareToPhoneArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^share\s+(?:this\s+)?(?:link|page|url)\s+to\s+(?:my\s+)?phone$",
            priority=454,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:qr|send)\s+(?:for\s+)?this\s+page$",
            priority=454,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^открой\s+(?:эту\s+)?ссылку\s+на\s+телефоне$",
            priority=454,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^відкрий\s+(?:це\s+)?посилання\s+на\s+телефоні$",
            priority=454,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: ShareToPhoneArgs) -> ToolResult:
        url = await asyncio.to_thread(_get_current_url)
        if not url:
            return ToolResult(
                success=False,
                error="No active browser tab. Open a page first.",
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = _qr_dir() / f"share_{timestamp}.png"

        err = await asyncio.to_thread(_generate_qr_image, url, out_path)
        if err:
            return ToolResult(success=False, error=err)

        await asyncio.to_thread(_open_image, out_path)

        return ToolResult(
            success=True,
            output=f"QR code for your phone is on screen. URL: {url}",
        )
