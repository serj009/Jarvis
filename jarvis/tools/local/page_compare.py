"""Page Comparison — compare two web pages via LLM.

Reads two pages (by URL or "current" for the active browser tab),
sends both texts to the local LLM, and asks it to build a structured
comparison table highlighting differences.

Dependencies (all optional, checked at runtime):
    - browser_controller (Selenium) for live page text
    - content_reader helpers (httpx + BeautifulSoup) as fallback
    - ollama_client for LLM comparison

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

_MAX_PAGE_CHARS = 8000  # per page, to fit in context window


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class ComparePageArgs(BaseModel):
    url1: str = Field(
        default="current",
        description=(
            'First URL to compare. Use "current" to read the active '
            "browser tab (requires Selenium)."
        ),
    )
    url2: str = Field(
        description="Second URL to compare.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_text(url: str) -> tuple[str, str, str | None]:
    """Fetch page text.  Returns (text, resolved_url, error)."""
    from jarvis.tools.local.content_reader import (
        _extract_readable,
        _httpx_fetch,
    )

    fetched = _httpx_fetch(url, timeout=20.0)
    if not fetched.get("success"):
        return "", url, fetched.get("error", "Fetch failed")
    html = fetched["html"]
    text = _extract_readable(html, max_chars=_MAX_PAGE_CHARS)
    return text, fetched.get("url", url), None


async def _collect_text(
    url: str, browser_controller: Any | None,
) -> tuple[str, str | None]:
    """Return (text, error) for *url*.  "current" uses the browser."""
    if url.lower() == "current":
        if browser_controller is None or not browser_controller.driver_active:
            return "", "No active browser tab — cannot read 'current'."
        res = browser_controller.get_page_text(max_chars=_MAX_PAGE_CHARS)
        if res.get("success"):
            return res["text"], None
        return "", res.get("error", "Failed to read current tab.")
    text, _, err = await asyncio.to_thread(_fetch_text, url)
    return text, err


async def _llm_compare(
    ollama_client: Any, text1: str, text2: str, url1: str, url2: str,
) -> str:
    """Ask the LLM to compare two pages and return the result text."""
    prompt = (
        "You are a comparison analyst. Compare the following two web pages "
        "and produce a clear, structured comparison. Use a table format if "
        "appropriate. Highlight key differences and similarities.\n\n"
        f"=== Page 1 ({url1}) ===\n{text1[:_MAX_PAGE_CHARS]}\n\n"
        f"=== Page 2 ({url2}) ===\n{text2[:_MAX_PAGE_CHARS]}\n\n"
        "Provide a concise comparison with categories/features as rows."
    )
    messages = [{"role": "user", "content": prompt}]
    parts: list[str] = []
    async for chunk in ollama_client.stream_chat(messages):
        if chunk.content:
            parts.append(chunk.content)
        if chunk.done:
            break
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ComparePagesTool:
    """Compare two web pages using an LLM."""

    name: str = "compare_pages"
    description: str = (
        "Compares two web pages (by URL or 'current' tab) using LLM analysis. "
        "Produces a structured comparison table of differences and similarities. "
        "(RU: сравни страницы, сравни вкладки, какая разница между / "
        "UK: порівняй сторінки, порівняй вкладки, яка різниця між)"
    )
    args_schema = ComparePageArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^compare\s+(?:these\s+)?pages$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^compare\s+(?:these\s+)?tabs$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^what(?:'s| is)\s+the\s+difference\s+between$",
            priority=460,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^сравни\s+страниц[ыу]$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^сравни\s+вкладк[иу]$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^какая\s+разница\s+между$",
            priority=460,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^порівняй\s+сторінк[иу]$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^порівняй\s+вкладк[иу]$",
            priority=460,
            args=lambda m: {"url1": "current"},
        ),
        VoicePattern(
            regex=r"^яка\s+різниця\s+між$",
            priority=460,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        ollama_client: Any = None,
        browser_controller: Any = None,
    ) -> None:
        self._ollama = ollama_client
        self._browser_controller = browser_controller

    async def execute(self, args: ComparePageArgs) -> ToolResult:
        if not self._ollama:
            return ToolResult(
                success=False,
                error="Page comparison requires an LLM client, but none is available.",
            )

        # Fetch both pages in parallel.
        text1_coro = _collect_text(args.url1, self._browser_controller)
        text2_coro = _collect_text(args.url2, self._browser_controller)
        (text1, err1), (text2, err2) = await asyncio.gather(text1_coro, text2_coro)

        if err1:
            return ToolResult(success=False, error=f"Page 1 error: {err1}")
        if err2:
            return ToolResult(success=False, error=f"Page 2 error: {err2}")
        if not text1.strip():
            return ToolResult(success=False, error="Page 1 appears empty.")
        if not text2.strip():
            return ToolResult(success=False, error="Page 2 appears empty.")

        try:
            comparison = await _llm_compare(
                self._ollama, text1, text2, args.url1, args.url2,
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"LLM comparison failed: {exc}")

        if not comparison:
            return ToolResult(success=False, error="LLM returned empty comparison.")

        return ToolResult(success=True, output=comparison)
