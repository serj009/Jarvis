"""Read and extract content from the current browser page.

Works in two modes:

- **Selenium active**: extracts text/title from the live DOM, can save
  the page as PDF via Chrome DevTools Protocol.
- **Fallback (httpx)**: fetches the URL with httpx and uses
  BeautifulSoup to extract readable text.  This path is also used
  when Selenium is not installed.

Offline handling (T7.4):
    Network-dependent operations catch connection errors and return a
    clear "no internet" message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class ContentReaderArgs(BaseModel):
    action: Literal["read_page", "get_title", "copy_text", "save_pdf"] = Field(
        description=(
            "read_page: extract main content text; get_title: page title; "
            "copy_text: copy page text to clipboard; save_pdf: save page as PDF."
        ),
    )
    url: str = Field(
        default="",
        description=(
            "URL to read. If empty, reads the current browser tab "
            "(requires Selenium). If provided, uses httpx fallback."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers — lazy imports
# ---------------------------------------------------------------------------

def _httpx_fetch(url: str, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch URL text with httpx (offline-safe)."""
    try:
        import httpx
    except ImportError:
        return {"success": False, "error": "httpx not installed"}
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return {"success": True, "html": resp.text, "url": str(resp.url)}
    except httpx.ConnectError:
        return {"success": False, "error": "No internet connection."}
    except httpx.TimeoutException:
        return {"success": False, "error": "Request timed out."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _extract_readable(html: str, max_chars: int = 5000) -> str:
    """Best-effort readable text from HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        # BeautifulSoup not installed — crude fallback
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_title(html: str) -> str:
    """Extract <title> from raw HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("title")
        return tag.get_text(strip=True) if tag else ""
    except ImportError:
        import re
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ContentReaderTool:
    name: str = "content_reader"
    description: str = (
        "Reads content from a web page. Can extract main text, page title, "
        "copy text to clipboard, or save page as PDF. If no URL is given, "
        "reads the current browser tab (Selenium required). "
        "(RU: прочитай страницу, о чём страница, скопируй текст, сохрани pdf / "
        "UK: прочитай сторінку, про що сторінка, скопіюй текст, збережи pdf)"
    )
    args_schema = ContentReaderArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:read|summarize|summarise)\s+(?:this\s+|the\s+)?page$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^what(?:'s| is)\s+(?:this\s+)?page\s+about\??$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^copy\s+(?:the\s+)?(?:page\s+)?text$",
            priority=465,
            args=lambda m: {"action": "copy_text"},
        ),
        # RU
        VoicePattern(
            regex=r"^прочитай\s+(?:эту\s+)?страниц[уа]$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^о\s+чём\s+(?:эта\s+)?страниц[аа]?\??$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^скопируй\s+(?:текст\s+)?(?:со?\s+)?страниц[ыуа]?$",
            priority=465,
            args=lambda m: {"action": "copy_text"},
        ),
        VoicePattern(
            regex=r"^сохрани\s+(?:страницу?\s+)?(?:как\s+)?(?:пдф|pdf)$",
            priority=465,
            args=lambda m: {"action": "save_pdf"},
        ),
        # UK
        VoicePattern(
            regex=r"^прочитай\s+(?:цю\s+)?сторінк[уа]$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^про\s+що\s+(?:ця\s+)?сторінк[аа]?\??$",
            priority=465,
            args=lambda m: {"action": "read_page"},
        ),
        VoicePattern(
            regex=r"^скопіюй\s+(?:текст\s+)?(?:зі?\s+)?сторінк[иуа]?$",
            priority=465,
            args=lambda m: {"action": "copy_text"},
        ),
    )

    async def execute(self, args: ContentReaderArgs) -> ToolResult:
        action = args.action

        if action == "read_page":
            return await self._read_page(args.url)
        if action == "get_title":
            return await self._get_title(args.url)
        if action == "copy_text":
            return await self._copy_text(args.url)
        if action == "save_pdf":
            return await self._save_pdf()
        return ToolResult(success=False, error=f"Unknown action {action!r}")

    # -- action implementations ---------------------------------------

    async def _read_page(self, url: str) -> ToolResult:
        text, err = await asyncio.to_thread(self._fetch_text, url)
        if err:
            return ToolResult(success=False, error=err)
        preview = text[:500] + ("…" if len(text) > 500 else "")
        return ToolResult(
            success=True,
            output=f"Page content ({len(text)} chars):\n{preview}",
        )

    async def _get_title(self, url: str) -> ToolResult:
        title, err = await asyncio.to_thread(self._fetch_title, url)
        if err:
            return ToolResult(success=False, error=err)
        return ToolResult(success=True, output=f"Page title: {title}")

    async def _copy_text(self, url: str) -> ToolResult:
        text, err = await asyncio.to_thread(self._fetch_text, url)
        if err:
            return ToolResult(success=False, error=err)
        # Copy to clipboard via platform
        try:
            from jarvis.platform import windows as winplat
            await asyncio.to_thread(winplat.clipboard_set, text[:10000])
        except Exception:
            log.debug("clipboard_set unavailable", exc_info=True)
        return ToolResult(
            success=True,
            output=f"Copied {len(text)} characters to clipboard, sir.",
        )

    async def _save_pdf(self) -> ToolResult:
        """Save as PDF — requires Selenium + Chrome DevTools Protocol."""
        from jarvis.tools.local.browser_tabs import _get_browser
        bc = _get_browser()
        if not bc.driver_active:
            return ToolResult(
                success=False,
                error="Cannot save as PDF — Selenium is not active.",
            )
        try:
            import base64
            from pathlib import Path
            result = await asyncio.to_thread(
                bc.execute_js,
                "return document.title;",
            )
            title = (result.get("result") or "page")[:50]
            safe_title = "".join(
                c if c.isalnum() or c in " -_" else "_" for c in title
            )
            save_dir = Path.home() / ".jarvis" / "downloads"
            save_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = save_dir / f"{safe_title}.pdf"

            pdf_result = await asyncio.to_thread(
                bc._driver.execute_cdp_cmd,
                "Page.printToPDF",
                {"printBackground": True, "preferCSSPageSize": True},
            )
            with open(pdf_path, "wb") as f:
                f.write(base64.b64decode(pdf_result["data"]))
            return ToolResult(
                success=True,
                output=f"Saved as {pdf_path}, sir.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"PDF save failed: {exc}")

    # -- data fetching ------------------------------------------------

    @staticmethod
    def _fetch_text(url: str) -> tuple[str, str | None]:
        """Return (text, error_or_none)."""
        # Try Selenium first
        from jarvis.tools.local.browser_tabs import _get_browser
        bc = _get_browser()
        if bc.driver_active and not url:
            result = bc.get_page_text(max_chars=5000)
            if result.get("success"):
                return result["text"], None

        # httpx fallback
        if not url:
            if bc.driver_active:
                info = bc.get_current_url()
                url = info.get("url", "")
            if not url:
                return "", "No URL provided and no active browser tab."

        fetched = _httpx_fetch(url)
        if not fetched.get("success"):
            return "", fetched.get("error", "Fetch failed")
        return _extract_readable(fetched["html"]), None

    @staticmethod
    def _fetch_title(url: str) -> tuple[str, str | None]:
        """Return (title, error_or_none)."""
        from jarvis.tools.local.browser_tabs import _get_browser
        bc = _get_browser()
        if bc.driver_active and not url:
            result = bc.get_page_title()
            if result.get("success"):
                return result["title"], None

        if not url:
            return "", "No URL and no active browser tab."
        fetched = _httpx_fetch(url)
        if not fetched.get("success"):
            return "", fetched.get("error", "Fetch failed")
        return _extract_title(fetched["html"]), None
