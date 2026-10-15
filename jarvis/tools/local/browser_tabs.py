"""Browser tab management tool — open/close/switch tabs, navigate.

Simple mode always works (webbrowser.open).  Advanced mode (Selenium)
enables close_tab, switch_tab, list_tabs, back, forward, refresh.

Offline handling (T7.4):
    When a URL cannot be opened, the tool returns a clear "no internet"
    message and the voice loop continues uninterrupted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton for the BrowserController
# ---------------------------------------------------------------------------
_browser_controller: Any = None


def _get_browser() -> Any:
    global _browser_controller
    if _browser_controller is None:
        from jarvis.browser import BrowserController
        _browser_controller = BrowserController(mode="auto")
    return _browser_controller


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class BrowserTabsArgs(BaseModel):
    action: Literal[
        "new_tab", "close_tab", "switch_tab",
        "back", "forward", "refresh", "list_tabs",
    ] = Field(description=(
        "Tab action. new_tab opens a URL; close_tab closes active tab; "
        "switch_tab switches to tab by index; back/forward navigate history; "
        "refresh reloads the page; list_tabs shows all open tabs."
    ))
    url: str = Field(
        default="",
        description="URL for new_tab (ignored for other actions).",
    )
    tab_index: int = Field(
        default=0,
        description="Tab index for switch_tab (0-based, ignored for others).",
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class BrowserTabsTool:
    name: str = "browser_tabs"
    description: str = (
        "Manages browser tabs and navigation. Use when the user asks to "
        "open a new tab, close a tab, switch between tabs, go back/forward "
        "in browser history, or refresh the page. "
        "(RU: вкладка, новая вкладка, закрой вкладку, назад, вперёд, обнови / "
        "UK: вкладка, нова вкладка, закрий вкладку, назад, вперед, оновити)"
    )
    args_schema = BrowserTabsArgs
    requires_confirmation: bool = False
    ends_turn_on_success: bool = True

    # -- Voice patterns (min 9: 3 EN + 3 RU + 3 UK) ------------------
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:open|new)\s+(?:a\s+)?(?:new\s+)?tab(?:\s+(?:with|for|to)\s+(.+))?$",
            priority=460,
            args=lambda m: {"action": "new_tab", "url": m.group(1) or ""},
        ),
        VoicePattern(
            regex=r"^close\s+(?:this\s+|the\s+)?tab$",
            priority=460,
            args=lambda m: {"action": "close_tab"},
        ),
        VoicePattern(
            regex=r"^(?:switch\s+(?:to\s+)?tab|tab)\s+(\d+)$",
            priority=460,
            args=lambda m: {"action": "switch_tab", "tab_index": int(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:go\s+)?back$",
            priority=470,
            args=lambda m: {"action": "back"},
        ),
        VoicePattern(
            regex=r"^(?:go\s+)?forward$",
            priority=470,
            args=lambda m: {"action": "forward"},
        ),
        VoicePattern(
            regex=r"^refresh(?:\s+(?:the\s+)?page)?$",
            priority=470,
            args=lambda m: {"action": "refresh"},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:открой|новая?)\s+(?:новую?\s+)?вкладк[уа]$",
            priority=460,
            args=lambda m: {"action": "new_tab"},
        ),
        VoicePattern(
            regex=r"^закрой\s+(?:эту?\s+)?вкладк[уа]$",
            priority=460,
            args=lambda m: {"action": "close_tab"},
        ),
        VoicePattern(
            regex=r"^(?:переключи(?:сь)?|перейди)\s+(?:на\s+)?вкладк[уа]\s+(\d+)$",
            priority=460,
            args=lambda m: {"action": "switch_tab", "tab_index": int(m.group(1))},
        ),
        VoicePattern(
            regex=r"^назад$",
            priority=470,
            args=lambda m: {"action": "back"},
        ),
        VoicePattern(
            regex=r"^вперёд$",
            priority=470,
            args=lambda m: {"action": "forward"},
        ),
        VoicePattern(
            regex=r"^обнови(?:\s+страницу)?$",
            priority=470,
            args=lambda m: {"action": "refresh"},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:відкрий|нова?)\s+(?:нову?\s+)?вкладк[уа]$",
            priority=460,
            args=lambda m: {"action": "new_tab"},
        ),
        VoicePattern(
            regex=r"^закрий\s+(?:цю?\s+)?вкладк[уа]$",
            priority=460,
            args=lambda m: {"action": "close_tab"},
        ),
        VoicePattern(
            regex=r"^(?:переключи(?:сь)?|перейди)\s+(?:на\s+)?вкладк[уа]\s+(\d+)$",
            priority=460,
            args=lambda m: {"action": "switch_tab", "tab_index": int(m.group(1))},
        ),
    )

    async def execute(self, args: BrowserTabsArgs) -> ToolResult:
        bc = _get_browser()
        action = args.action

        try:
            result = await asyncio.to_thread(self._dispatch, bc, action, args)
        except Exception as exc:
            log.exception("browser_tabs %s failed", action)
            return ToolResult(success=False, error=f"Browser error: {exc}")

        if not result.get("success", False):
            return ToolResult(
                success=False,
                error=result.get("error", "Unknown browser error"),
            )
        return ToolResult(success=True, output=self._spoken(action, result))

    @staticmethod
    def _dispatch(
        bc: Any, action: str, args: BrowserTabsArgs,
    ) -> dict[str, Any]:
        if action == "new_tab":
            url = args.url or "about:blank"
            return bc.open_tab(url)
        if action == "close_tab":
            return bc.close_tab()
        if action == "switch_tab":
            return bc.switch_tab(args.tab_index)
        if action == "back":
            return bc.go_back()
        if action == "forward":
            return bc.go_forward()
        if action == "refresh":
            return bc.refresh()
        if action == "list_tabs":
            return bc.list_tabs()
        return {"success": False, "error": f"Unknown action {action!r}"}

    @staticmethod
    def _spoken(action: str, result: dict[str, Any]) -> str:
        if action == "new_tab":
            return "Opened a new tab, sir."
        if action == "close_tab":
            remaining = result.get("remaining_tabs", "?")
            return f"Tab closed. {remaining} tab(s) remaining, sir."
        if action == "switch_tab":
            title = result.get("title", "")
            return f"Switched to tab: {title}, sir." if title else "Switched tab, sir."
        if action == "list_tabs":
            count = result.get("count", 0)
            return f"{count} tab(s) open, sir."
        return f"{action.replace('_', ' ').title()} done, sir."
