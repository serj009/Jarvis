"""Bookmark management — save, list, open, delete browser bookmarks.

Bookmarks are stored in a JSON file at ``~/.jarvis/data/bookmarks.json``.
Each entry has: name, url, title, summary (optional), saved_at.
"""

from __future__ import annotations

import asyncio
import json
import logging
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_BOOKMARKS_DIR = Path.home() / ".jarvis" / "data"
_BOOKMARKS_FILE = _BOOKMARKS_DIR / "bookmarks.json"


def _load_bookmarks() -> list[dict[str, Any]]:
    """Load bookmarks from disk (empty list on first run)."""
    if not _BOOKMARKS_FILE.exists():
        return []
    try:
        return json.loads(_BOOKMARKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Corrupt bookmarks file — starting fresh")
        return []


def _save_bookmarks(bookmarks: list[dict[str, Any]]) -> None:
    _BOOKMARKS_DIR.mkdir(parents=True, exist_ok=True)
    _BOOKMARKS_FILE.write_text(
        json.dumps(bookmarks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class BookmarksArgs(BaseModel):
    action: Literal["save", "list", "open", "delete"] = Field(
        description=(
            "save: save current page or given URL to bookmarks; "
            "list: show all bookmarks; "
            "open: open a bookmark by name; "
            "delete: remove a bookmark by name."
        ),
    )
    name: str = Field(
        default="",
        description="Bookmark name (for save/open/delete). For save, auto-generated if empty.",
    )
    url: str = Field(
        default="",
        description="URL to bookmark (for save). If empty, tries current browser tab.",
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class BookmarksTool:
    name: str = "bookmarks"
    description: str = (
        "Manages browser bookmarks — save, list, open, or delete bookmarks. "
        "Bookmarks are stored locally in JSON and persist across sessions. "
        "(RU: закладка, сохрани в закладки, покажи закладки, открой закладку, "
        "запомни страницу / UK: закладка, збережи в закладки, покажи закладки, "
        "відкрий закладку, запам'ятай сторінку)"
    )
    args_schema = BookmarksArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:save|add)\s+(?:this\s+)?(?:page\s+)?(?:to\s+|as\s+)?bookmark[s]?$",
            priority=462,
            args=lambda m: {"action": "save"},
        ),
        VoicePattern(
            regex=r"^(?:show|list|open)\s+(?:my\s+)?bookmarks$",
            priority=462,
            args=lambda m: {"action": "list"},
        ),
        VoicePattern(
            regex=r"^open\s+bookmark\s+(.+)$",
            priority=462,
            args=lambda m: {"action": "open", "name": m.group(1)},
        ),
        # RU
        VoicePattern(
            regex=r"^сохрани\s+(?:в\s+)?закладк[иу]$",
            priority=462,
            args=lambda m: {"action": "save"},
        ),
        VoicePattern(
            regex=r"^(?:покажи|открой)\s+(?:мои\s+)?закладк[иу]$",
            priority=462,
            args=lambda m: {"action": "list"},
        ),
        VoicePattern(
            regex=r"^(?:запомни|сохрани)\s+(?:эту\s+)?страниц[уа]$",
            priority=462,
            args=lambda m: {"action": "save"},
        ),
        VoicePattern(
            regex=r"^открой\s+закладк[уа]\s+(.+)$",
            priority=462,
            args=lambda m: {"action": "open", "name": m.group(1)},
        ),
        # UK
        VoicePattern(
            regex=r"^збережи\s+(?:в\s+)?закладк[иу]$",
            priority=462,
            args=lambda m: {"action": "save"},
        ),
        VoicePattern(
            regex=r"^(?:покажи|відкрий)\s+(?:мої\s+)?закладк[иу]$",
            priority=462,
            args=lambda m: {"action": "list"},
        ),
        VoicePattern(
            regex=r"^(?:запам'?ятай|збережи)\s+(?:цю\s+)?сторінк[уа]$",
            priority=462,
            args=lambda m: {"action": "save"},
        ),
    )

    async def execute(self, args: BookmarksArgs) -> ToolResult:
        action = args.action
        try:
            if action == "save":
                return await self._save(args.url, args.name)
            if action == "list":
                return await asyncio.to_thread(self._list)
            if action == "open":
                return await self._open(args.name)
            if action == "delete":
                return await asyncio.to_thread(self._delete, args.name)
        except Exception as exc:
            log.exception("bookmarks %s failed", action)
            return ToolResult(success=False, error=f"Bookmark error: {exc}")
        return ToolResult(success=False, error=f"Unknown action {action!r}")

    # -- actions ------------------------------------------------------

    async def _save(self, url: str, name: str) -> ToolResult:
        title = ""
        # Try to get URL/title from browser if not provided
        if not url:
            try:
                from jarvis.tools.local.browser_tabs import _get_browser
                bc = _get_browser()
                if bc.driver_active:
                    info = bc.get_current_url()
                    url = info.get("url", "")
                    title_info = bc.get_page_title()
                    title = title_info.get("title", "")
            except Exception:
                pass
        if not url:
            return ToolResult(
                success=False,
                error="No URL provided and no active browser tab.",
            )
        if not name:
            name = title or url[:60]

        bookmark = {
            "name": name,
            "url": url,
            "title": title,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        def _do_save() -> None:
            bm = _load_bookmarks()
            # Deduplicate by URL
            bm = [b for b in bm if b.get("url") != url]
            bm.append(bookmark)
            _save_bookmarks(bm)

        await asyncio.to_thread(_do_save)
        return ToolResult(
            success=True,
            output=f"Bookmarked \"{name}\", sir.",
        )

    @staticmethod
    def _list() -> ToolResult:
        bm = _load_bookmarks()
        if not bm:
            return ToolResult(success=True, output="No bookmarks saved yet, sir.")
        lines = [f"{i+1}. {b['name']} — {b['url']}" for i, b in enumerate(bm)]
        return ToolResult(
            success=True,
            output=f"{len(bm)} bookmark(s):\n" + "\n".join(lines),
        )

    async def _open(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(success=False, error="Bookmark name required.")
        bm = await asyncio.to_thread(_load_bookmarks)
        name_lower = name.lower()
        match = next(
            (b for b in bm if name_lower in b.get("name", "").lower()),
            None,
        )
        if not match:
            return ToolResult(
                success=False,
                error=f"Bookmark \"{name}\" not found.",
            )
        await asyncio.to_thread(webbrowser.open, match["url"])
        return ToolResult(
            success=True,
            output=f"Opening bookmark \"{match['name']}\", sir.",
        )

    @staticmethod
    def _delete(name: str) -> ToolResult:
        if not name:
            return ToolResult(success=False, error="Bookmark name required.")
        bm = _load_bookmarks()
        name_lower = name.lower()
        original_len = len(bm)
        bm = [b for b in bm if name_lower not in b.get("name", "").lower()]
        if len(bm) == original_len:
            return ToolResult(success=False, error=f"Bookmark \"{name}\" not found.")
        _save_bookmarks(bm)
        return ToolResult(
            success=True,
            output=f"Deleted bookmark \"{name}\", sir.",
        )
