"""Save structured knowledge from a web page into Jarvis memory.

Reads the current browser page (Selenium or httpx fallback), sends the
text to a local LLM for structured extraction, and persists each
extracted item as a separate memory fact with relations.

Works for ANY content type: game guides, recipes, tech tutorials,
collections, reference pages, etc.  The LLM decides the structure.

Dependencies (all optional, checked at runtime):
    - browser_controller (Selenium) for live page text
    - content_reader helpers (httpx + BeautifulSoup) as fallback
    - ollama_client for LLM extraction
    - memory_store + relation_store for persistence

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


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class SavePageKnowledgeArgs(BaseModel):
    url: str = Field(
        default="",
        description=(
            "URL to extract knowledge from.  If empty, reads the current "
            "browser tab (requires Selenium) or prompts for a URL."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SavePageKnowledgeTool:
    """Extract structured knowledge from a web page and save to memory."""

    name: str = "save_page_knowledge"
    description: str = (
        "Reads a web page, extracts structured knowledge (items, facts, "
        "relationships) using an LLM, and saves them to Jarvis memory. "
        "Works for game guides, recipes, tech docs, collections — any "
        "structured content. "
        "(RU: сохрани информацию со страницы, запомни что на странице / "
        "UK: збережи інформацію зі сторінки, запамʼятай що на сторінці)"
    )
    args_schema = SavePageKnowledgeArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — 4 patterns
        VoicePattern(
            regex=r"^save\s+(?:this\s+)?(?:page\s+)?(?:information|knowledge|info)$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^save\s+(?:information|knowledge)\s+from\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^remember\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^extract\s+knowledge\s+from\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {},
        ),
        # RU — 4 patterns
        VoicePattern(
            regex=r"^сохрани\s+информацию\s+(?:с[ое]?\s+)?страниц[ыу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запомни\s+(?:эту\s+)?страницу$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^сохрани\s+знания\s+(?:с[ое]?\s+)?страниц[ыу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запомни\s+что\s+на\s+страниц[еу]$",
            priority=460,
            args=lambda m: {},
        ),
        # UK — 4 patterns
        VoicePattern(
            regex=r"^збережи\s+інформацію\s+(?:зі?\s+)?сторінк[иу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запам['ʼ]?ятай\s+(?:цю\s+)?сторінку$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^збережи\s+знання\s+(?:зі?\s+)?сторінк[иу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запам['ʼ]?ятай\s+що\s+на\s+сторінц[іу]$",
            priority=460,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        ollama_client: Any = None,
        memory_store: Any = None,
        relation_store: Any = None,
        browser_controller: Any = None,
    ) -> None:
        self._ollama = ollama_client
        self._memory_store = memory_store
        self._relation_store = relation_store
        self._browser_controller = browser_controller

    # -- execute ------------------------------------------------------------

    async def execute(self, args: SavePageKnowledgeArgs) -> ToolResult:
        """Main entry point: fetch page → extract → save."""
        # 0. Pre-flight checks
        if not self._ollama:
            return ToolResult(
                success=False,
                error="Knowledge extraction requires an LLM client, but none is available.",
            )
        if not self._memory_store:
            return ToolResult(
                success=False,
                error="Knowledge extraction requires a memory store, but none is available.",
            )

        # 1. Get page content
        page_text, page_url, page_title, err = await self._get_page_content(
            args.url
        )
        if err:
            return ToolResult(success=False, error=err)
        if not page_text or not page_text.strip():
            return ToolResult(
                success=False,
                error="Page appears to be empty — no text content found.",
            )

        # 2. Extract and save via KnowledgeExtractor
        from jarvis.memory.knowledge_extractor import KnowledgeExtractor

        extractor = KnowledgeExtractor(
            ollama_client=self._ollama,
            memory_store=self._memory_store,
            relation_store=self._relation_store,
        )
        result = await extractor.extract_and_save(page_text, page_url, page_title)

        # 3. Format response
        if result.get("error"):
            return ToolResult(success=False, error=result["error"])

        topic = result.get("topic", "unknown")
        items_saved = result.get("items_saved", 0)
        relations = result.get("relations_created", 0)
        is_fallback = result.get("fallback", False)

        if is_fallback:
            return ToolResult(
                success=True,
                output=(
                    f"Page saved as a single fact (LLM could not extract structure). "
                    f"Topic: {topic}"
                ),
            )

        return ToolResult(
            success=True,
            output=(
                f"Saved {items_saved} facts about \"{topic}\" with "
                f"{relations} relations, sir."
            ),
        )

    # -- page content retrieval ---------------------------------------------

    async def _get_page_content(
        self, url: str
    ) -> tuple[str, str, str, str | None]:
        """Fetch page text, url, title.  Returns (text, url, title, error).

        Tries Selenium first (live DOM), then httpx fallback.
        """
        # Try Selenium browser controller
        if self._browser_controller is not None:
            text, page_url, title, err = await self._try_selenium(url)
            if err is None:
                return text, page_url, title, None

        # httpx + BeautifulSoup fallback
        if url:
            return await asyncio.to_thread(self._fetch_with_httpx, url)

        # Try to get URL from browser controller even if text extraction failed
        if self._browser_controller is not None:
            try:
                url_info = self._browser_controller.get_current_url()
                if url_info.get("success") and url_info.get("url"):
                    return await asyncio.to_thread(
                        self._fetch_with_httpx, url_info["url"]
                    )
            except Exception:
                pass

        return "", "", "", "No URL provided and no active browser tab."

    async def _try_selenium(
        self, url: str
    ) -> tuple[str, str, str, str | None]:
        """Try to get content via Selenium.  Returns (text, url, title, error)."""
        bc = self._browser_controller
        if not bc.driver_active and not url:
            return "", "", "", "No active browser session"

        try:
            if url:
                bc.open_tab(url)
                # Small wait for page load
                await asyncio.sleep(1)

            # Get URL
            url_result = bc.get_current_url()
            page_url = url_result.get("url", url or "")

            # Get title
            title_result = bc.get_page_title()
            page_title = title_result.get("title", "")

            # Get text (generous limit for extraction)
            text_result = bc.get_page_text(max_chars=15_000)
            if not text_result.get("success"):
                return "", page_url, page_title, text_result.get("error", "Failed to get page text")

            return text_result["text"], page_url, page_title, None
        except Exception as exc:
            return "", "", "", f"Selenium error: {exc}"

    @staticmethod
    def _fetch_with_httpx(url: str) -> tuple[str, str, str, str | None]:
        """Fetch and extract text via httpx + BeautifulSoup."""
        from jarvis.tools.local.content_reader import (
            _extract_readable,
            _extract_title,
            _httpx_fetch,
        )

        fetched = _httpx_fetch(url, timeout=20.0)
        if not fetched.get("success"):
            return "", url, "", fetched.get("error", "Fetch failed")

        html = fetched["html"]
        text = _extract_readable(html, max_chars=15_000)
        title = _extract_title(html)
        final_url = fetched.get("url", url)

        return text, final_url, title, None
