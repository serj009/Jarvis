"""Advanced tab management — close other tabs, count, find, duplicate.

Extends browser_tabs.py with power-user tab operations:
- close_other_tabs: close all tabs except the current one
- count_tabs: report the number of open tabs
- find_tab: find a tab by title or URL substring
- duplicate_tab: duplicate the current tab

Requires Selenium for all operations; falls back with a clear message
when Selenium is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton reuse from browser_tabs
# ---------------------------------------------------------------------------
_browser_controller: Any = None


def _get_browser() -> Any:
    global _browser_controller
    if _browser_controller is None:
        from jarvis.browser import BrowserController
        _browser_controller = BrowserController(mode="auto")
    return _browser_controller


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class CloseOtherTabsArgs(BaseModel):
    """No arguments needed — closes all tabs except the active one."""
    pass


class CountTabsArgs(BaseModel):
    """No arguments needed — returns the number of open tabs."""
    pass


class FindTabArgs(BaseModel):
    query: str = Field(
        description="Substring to search for in tab titles and URLs.",
    )


class DuplicateTabArgs(BaseModel):
    """No arguments needed — duplicates the current active tab."""
    pass


# ---------------------------------------------------------------------------
# Helper: check Selenium availability
# ---------------------------------------------------------------------------

def _require_selenium(bc: Any) -> str | None:
    """Return an error string if Selenium is not active, else None."""
    if not getattr(bc, "driver_active", False):
        return (
            "Selenium is not active. Advanced tab management requires "
            "a Selenium-controlled browser session."
        )
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class CloseOtherTabsTool:
    name: str = "close_other_tabs"
    description: str = (
        "Closes all browser tabs except the currently active one. "
        "Useful for decluttering. Requires Selenium. "
        "(RU: закрой остальные вкладки, закрой все кроме текущей / "
        "UK: закрий інші вкладки, закрий все крім поточної)"
    )
    args_schema = CloseOtherTabsArgs
    requires_confirmation: bool = False
    ends_turn_on_success: bool = True

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^close\s+(?:all\s+)?other\s+tabs$",
            priority=458,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^close\s+(?:all\s+)?tabs\s+(?:except|but)\s+(?:this|current)(?:\s+one)?$",
            priority=458,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^закрой\s+(?:все\s+)?остальные\s+вкладк[иу]$",
            priority=458,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^закрой\s+все\s+(?:вкладки\s+)?кроме\s+(?:этой|текущей)$",
            priority=458,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^закрий\s+(?:усі\s+)?інші\s+вкладк[иу]$",
            priority=458,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^закрий\s+все\s+(?:вкладки\s+)?крім\s+(?:цієї|поточної)$",
            priority=458,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: CloseOtherTabsArgs) -> ToolResult:
        bc = _get_browser()
        err = _require_selenium(bc)
        if err:
            return ToolResult(success=False, error=err)
        try:
            result = await asyncio.to_thread(self._close_others, bc)
        except Exception as exc:
            log.exception("close_other_tabs failed")
            return ToolResult(success=False, error=f"Browser error: {exc}")
        return result

    @staticmethod
    def _close_others(bc: Any) -> ToolResult:
        driver = bc.driver
        handles = driver.window_handles
        current = driver.current_window_handle
        closed = 0
        for h in handles:
            if h != current:
                driver.switch_to.window(h)
                driver.close()
                closed += 1
        driver.switch_to.window(current)
        return ToolResult(
            success=True,
            output=f"Closed {closed} tab(s). Only the current tab remains.",
        )


class CountTabsTool:
    name: str = "count_tabs"
    description: str = (
        "Counts the number of currently open browser tabs. "
        "Requires Selenium. "
        "(RU: сколько вкладок открыто / UK: скільки вкладок відкрито)"
    )
    args_schema = CountTabsArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^how\s+many\s+tabs?\s+(?:are\s+)?open(?:ed)?$",
            priority=458,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^count\s+(?:my\s+)?(?:open\s+)?tabs$",
            priority=458,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^сколько\s+вкладок\s+открыто$",
            priority=458,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^скільки\s+вкладок\s+відкрито$",
            priority=458,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: CountTabsArgs) -> ToolResult:
        bc = _get_browser()
        err = _require_selenium(bc)
        if err:
            return ToolResult(success=False, error=err)
        try:
            count = await asyncio.to_thread(
                lambda: len(bc.driver.window_handles),
            )
        except Exception as exc:
            log.exception("count_tabs failed")
            return ToolResult(success=False, error=f"Browser error: {exc}")
        return ToolResult(
            success=True,
            output=f"You have {count} tab(s) open, sir.",
        )


class FindTabTool:
    name: str = "find_tab"
    description: str = (
        "Finds a browser tab whose title or URL contains the given query "
        "string and switches to it. Requires Selenium. "
        "(RU: найди вкладку с ... / UK: знайди вкладку з ...)"
    )
    args_schema = FindTabArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^find\s+(?:the\s+)?tab\s+(?:with|for|about)\s+(.+)$",
            priority=458,
            args=lambda m: {"query": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^switch\s+to\s+(?:the\s+)?(.+?)\s+tab$",
            priority=458,
            args=lambda m: {"query": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^найди\s+вкладку\s+(?:с|про|где)\s+(.+)$",
            priority=458,
            args=lambda m: {"query": m.group(1).strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^знайди\s+вкладку\s+(?:з|про|де)\s+(.+)$",
            priority=458,
            args=lambda m: {"query": m.group(1).strip()},
        ),
    )

    async def execute(self, args: FindTabArgs) -> ToolResult:
        bc = _get_browser()
        err = _require_selenium(bc)
        if err:
            return ToolResult(success=False, error=err)
        try:
            result = await asyncio.to_thread(self._find, bc, args.query)
        except Exception as exc:
            log.exception("find_tab failed")
            return ToolResult(success=False, error=f"Browser error: {exc}")
        return result

    @staticmethod
    def _find(bc: Any, query: str) -> ToolResult:
        driver = bc.driver
        query_lower = query.lower()
        original = driver.current_window_handle
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            title = driver.title or ""
            url = driver.current_url or ""
            if query_lower in title.lower() or query_lower in url.lower():
                return ToolResult(
                    success=True,
                    output=f"Found and switched to tab: \"{title}\" ({url})",
                )
        # Not found — switch back
        driver.switch_to.window(original)
        return ToolResult(
            success=False,
            error=f"No tab found matching \"{query}\".",
        )


class DuplicateTabTool:
    name: str = "duplicate_tab"
    description: str = (
        "Duplicates the currently active browser tab by opening a new tab "
        "with the same URL. Requires Selenium. "
        "(RU: дублируй вкладку / UK: дублюй вкладку)"
    )
    args_schema = DuplicateTabArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^duplicate\s+(?:this\s+|the\s+)?tab$",
            priority=458,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^дублируй\s+(?:эту\s+)?вкладку$",
            priority=458,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^дублюй\s+(?:цю\s+)?вкладку$",
            priority=458,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: DuplicateTabArgs) -> ToolResult:
        bc = _get_browser()
        err = _require_selenium(bc)
        if err:
            return ToolResult(success=False, error=err)
        try:
            result = await asyncio.to_thread(self._dup, bc)
        except Exception as exc:
            log.exception("duplicate_tab failed")
            return ToolResult(success=False, error=f"Browser error: {exc}")
        return result

    @staticmethod
    def _dup(bc: Any) -> ToolResult:
        driver = bc.driver
        url = driver.current_url
        if not url:
            return ToolResult(success=False, error="No URL in the current tab.")
        # Open new tab with same URL via JavaScript
        driver.execute_script(f"window.open('{url}', '_blank');")
        # Switch to the new tab (last handle)
        handles = driver.window_handles
        driver.switch_to.window(handles[-1])
        return ToolResult(
            success=True,
            output=f"Duplicated tab: {url}",
        )
