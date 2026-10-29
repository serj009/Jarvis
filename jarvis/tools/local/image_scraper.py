"""Download all images from a web page.

Reads the HTML of a page (via Selenium or httpx), extracts all <img>
tags, filters out icons/tracking pixels, and downloads the remaining
images into a timestamped folder under ~/Downloads/.

Dependencies (all optional, checked at runtime):
    - httpx for HTTP fetching
    - beautifulsoup4 for HTML parsing
    - selenium (optional) for live DOM access
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

_DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"

# Minimum dimension (width or height) — skip tiny icons / tracking pixels.
_MIN_DIMENSION = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_image_urls(html: str, base_url: str) -> list[str]:
    """Extract image URLs from HTML, resolving relative paths."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Lightweight regex fallback when beautifulsoup4 is not installed.
        urls: list[str] = []
        seen: set[str] = set()
        for m in re.finditer(r'<img\s[^>]*?src="([^"]+)"[^>]*/?>', html, re.IGNORECASE):
            tag = m.group(0)
            # Honour width/height attributes to skip tiny images.
            w_m = re.search(r'width="(\d+)', tag, re.IGNORECASE)
            h_m = re.search(r'height="(\d+)', tag, re.IGNORECASE)
            w = int(w_m.group(1)) if w_m else None
            h = int(h_m.group(1)) if h_m else None
            if (w is not None and w < _MIN_DIMENSION) or (h is not None and h < _MIN_DIMENSION):
                continue
            src = m.group(1)
            if src.startswith("data:"):
                continue
            absolute = urljoin(base_url, src)
            if absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)
        for m in re.finditer(r'<img\s[^>]*?srcset="([^"]+)"[^>]*/?>', html, re.IGNORECASE):
            for entry in m.group(1).split(","):
                parts = entry.strip().split()
                if parts:
                    src_url = urljoin(base_url, parts[0])
                    if src_url not in seen:
                        seen.add(src_url)
                        urls.append(src_url)
        return urls

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src", "")
        srcset = img.get("srcset", "")

        # Skip base64 data URIs.
        if src.startswith("data:"):
            continue

        # Check dimension attributes to skip tiny images.
        width = _parse_dimension(img.get("width", ""))
        height = _parse_dimension(img.get("height", ""))
        if (width and width < _MIN_DIMENSION) or (height and height < _MIN_DIMENSION):
            continue

        if src:
            absolute = urljoin(base_url, src)
            if absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)

        # Also extract from srcset (take the largest variant).
        if srcset:
            for entry in srcset.split(","):
                entry = entry.strip()
                parts = entry.split()
                if parts:
                    src_url = urljoin(base_url, parts[0])
                    if src_url not in seen:
                        seen.add(src_url)
                        urls.append(src_url)

    return urls


def _parse_dimension(value: str) -> int | None:
    """Parse a width/height attribute into an integer, or None."""
    if not value:
        return None
    # Strip "px" suffix if present.
    value = value.strip().rstrip("px").strip()
    try:
        return int(value)
    except ValueError:
        return None


def _is_tracking_pixel(url: str) -> bool:
    """Heuristic: skip common tracking / analytics pixels."""
    lower = url.lower()
    tracking_patterns = (
        "pixel", "tracking", "beacon", "analytics",
        "1x1", "spacer", "blank.gif", "clear.gif",
    )
    return any(p in lower for p in tracking_patterns)


def _safe_filename(url: str, index: int) -> str:
    """Generate a safe filename from a URL."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if "/" in path else ""

    if not name or "." not in name:
        # No recognisable filename — generate one.
        name = f"image_{index:03d}.jpg"

    # Sanitise: keep only safe chars.
    name = re.sub(r"[^\w.\-]", "_", name)

    # Limit length.
    if len(name) > 100:
        base, ext = os.path.splitext(name)
        name = base[:95] + ext

    return name


def _download_images(
    image_urls: list[str],
    dest_dir: Path,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Download images to *dest_dir*.

    Returns {"downloaded": N, "failed": N, "skipped": N, "dir": str}.
    """
    try:
        import httpx
    except ImportError:
        return {"error": "httpx not installed."}

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    failed = 0
    skipped = 0

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    for i, url in enumerate(image_urls):
        if _is_tracking_pixel(url):
            skipped += 1
            continue

        filename = _safe_filename(url, i)
        dest_path = dest_dir / filename

        # Avoid overwriting.
        base, ext = os.path.splitext(filename)
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{base}_{counter}{ext}"
            counter += 1

        try:
            resp = httpx.get(
                url, timeout=timeout, follow_redirects=True, headers=headers
            )
            resp.raise_for_status()

            # Verify content-type is an image.
            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                skipped += 1
                continue

            # Skip if too small (< 1KB likely a tracking pixel).
            if len(resp.content) < 1024:
                skipped += 1
                continue

            dest_path.write_bytes(resp.content)
            downloaded += 1
            log.debug("Downloaded image %d: %s", i, dest_path.name)

        except Exception as exc:
            log.debug("Failed to download %s: %s", url, exc)
            failed += 1

    return {
        "downloaded": downloaded,
        "failed": failed,
        "skipped": skipped,
        "dir": str(dest_dir),
    }


def _get_page_html(
    url: str,
    browser_controller: Any = None,
) -> tuple[str, str, str | None]:
    """Get HTML and resolved URL.

    Returns (html, resolved_url, error_or_none).
    """
    # Try Selenium first.
    bc = browser_controller
    if bc is not None and getattr(bc, "driver_active", False):
        try:
            source_result = bc.get_page_source()
            if source_result.get("success"):
                url_result = bc.get_current_url()
                resolved = url_result.get("url", url)
                return source_result["html"], resolved, None
        except Exception:
            pass

    # httpx fallback.
    if not url:
        return "", "", "No URL provided and no active browser tab."

    try:
        import httpx
    except ImportError:
        return "", url, "httpx not installed."

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True, headers=headers)
        resp.raise_for_status()
        return resp.text, str(resp.url), None
    except httpx.ConnectError:
        return "", url, "No internet connection."
    except httpx.TimeoutException:
        return "", url, "Request timed out."
    except Exception as exc:
        return "", url, str(exc)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class ScrapeImagesArgs(BaseModel):
    url: str = Field(
        default="",
        description=(
            "URL of the page to scrape images from. If empty, uses the "
            "current browser tab (requires Selenium)."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ScrapeImagesTool:
    """Download all images from a web page."""

    name: str = "scrape_images"
    description: str = (
        "Downloads all images from a web page. Filters out icons, "
        "tracking pixels, and tiny images. Saves to ~/Downloads/ in a "
        "timestamped folder. Provide a URL or leave empty to use the "
        "current browser tab. "
        "(RU: скачай все картинки со страницы / "
        "UK: скачай всі картинки зі сторінки)"
    )
    args_schema = ScrapeImagesArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — 3 patterns
        VoicePattern(
            regex=r"^download\s+all\s+images?$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^save\s+images?\s+from\s+(?:this\s+)?page$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^scrape\s+images?$",
            priority=480,
            args=lambda m: {},
        ),
        # RU — 3 patterns
        VoicePattern(
            regex=r"^скачай\s+все\s+картинки$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^сохрани\s+картинки\s+(?:со?\s+)?страниц[ыу]$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^скачай\s+изображения$",
            priority=480,
            args=lambda m: {},
        ),
        # UK — 3 patterns
        VoicePattern(
            regex=r"^скачай\s+всі\s+картинки$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^збережи\s+картинки\s+(?:зі?\s+)?сторінк[иу]$",
            priority=480,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^скачай\s+зображення$",
            priority=480,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        browser_controller: Any = None,
    ) -> None:
        self._browser_controller = browser_controller

    async def execute(self, args: ScrapeImagesArgs) -> ToolResult:
        """Scrape images from the page."""
        url = args.url.strip()

        # Get page HTML.
        html, resolved_url, err = await asyncio.to_thread(
            _get_page_html, url, self._browser_controller
        )
        if err:
            return ToolResult(success=False, error=err)
        if not html:
            return ToolResult(
                success=False,
                error="No page content found.",
            )

        # Extract image URLs.
        image_urls = _extract_image_urls(html, resolved_url)
        if not image_urls:
            return ToolResult(
                success=True,
                output="No downloadable images found on this page.",
            )

        # Create a destination folder.
        domain = urlparse(resolved_url).hostname or "unknown"
        domain = re.sub(r"[^\w.\-]", "_", domain)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = _DEFAULT_DOWNLOAD_DIR / f"images_{domain}_{timestamp}"

        # Download images.
        result = await asyncio.to_thread(
            _download_images, image_urls, dest_dir
        )

        if result.get("error"):
            return ToolResult(success=False, error=result["error"])

        downloaded = result.get("downloaded", 0)
        failed = result.get("failed", 0)
        skipped = result.get("skipped", 0)
        total = len(image_urls)

        if downloaded == 0:
            return ToolResult(
                success=True,
                output=(
                    f"Found {total} image(s) but none could be downloaded "
                    f"({skipped} skipped, {failed} failed)."
                ),
            )

        log.info(
            "Scraped %d images to %s (skipped=%d, failed=%d)",
            downloaded, dest_dir, skipped, failed,
        )
        return ToolResult(
            success=True,
            output=(
                f"Downloaded {downloaded}/{total} images to "
                f"{dest_dir}, sir."
                + (f" ({skipped} skipped, {failed} failed)" if skipped + failed > 0 else "")
            ),
        )
