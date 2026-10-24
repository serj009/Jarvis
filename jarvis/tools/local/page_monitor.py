"""Web Page Monitor — track pages for changes.

Saves a text snapshot + hash of a web page, checks periodically,
and reports diffs when the content changes.  Uses httpx + BeautifulSoup
for page fetching (same stack as content_reader), persists monitor
state in ``~/.jarvis/page_monitors.json``.

Periodic checking runs via asyncio tasks (same pattern as timers.py).
An optional ``on_change`` callback allows TTS notifications.

Dependencies (all optional, checked at runtime):
    - httpx for page fetching
    - beautifulsoup4 for readable-text extraction

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from difflib import unified_diff
from pathlib import Path
from typing import Any, Callable, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)


def _default_monitors_path() -> Path:
    # Single data-dir resolver (jarvis.paths); layout unchanged. Call-time.
    from jarvis.paths import data_path

    return data_path("page_monitors.json")


# ---------------------------------------------------------------------------
# Monitor store
# ---------------------------------------------------------------------------

class _MonitorEntry:
    """Internal representation of a monitored page."""

    __slots__ = (
        "url", "interval_minutes", "last_text", "last_hash",
        "last_checked", "task", "active",
    )

    def __init__(
        self,
        url: str,
        interval_minutes: float,
        last_text: str = "",
        last_hash: str = "",
        last_checked: float = 0.0,
    ) -> None:
        self.url = url
        self.interval_minutes = interval_minutes
        self.last_text = last_text
        self.last_hash = last_hash
        self.last_checked = last_checked
        self.task: asyncio.Task | None = None
        self.active: bool = True

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "interval_minutes": self.interval_minutes,
            "last_text": self.last_text,
            "last_hash": self.last_hash,
            "last_checked": self.last_checked,
        }


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_page_text(url: str, timeout: float = 20.0) -> tuple[str, str | None]:
    """Fetch page and extract readable text.  Returns (text, error)."""
    try:
        import httpx
    except ImportError:
        return "", "httpx is not installed."
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return "", f"Fetch error: {exc}"
    html = resp.text
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    return text[:50_000], None


def _compute_diff(old_text: str, new_text: str) -> str:
    """Compute a human-readable unified diff."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(unified_diff(old_lines, new_lines, fromfile="before", tofile="after", lineterm=""))
    if not diff:
        return ""
    # Keep only the first 60 diff lines to avoid flooding.
    if len(diff) > 60:
        diff = diff[:60] + [f"\n... ({len(diff) - 60} more lines)"]
    return "\n".join(diff)


class MonitorStore:
    """Manages page monitors with optional persistence."""

    def __init__(
        self,
        *,
        on_change: Callable[[str, str], None] | None = None,
        persist_path: Path | None = None,
    ) -> None:
        self._monitors: dict[str, _MonitorEntry] = {}
        self._on_change = on_change
        self._persist_path = persist_path if persist_path is not None else _default_monitors_path()

    # -- persistence -------------------------------------------------------

    def _save(self) -> None:
        try:
            store = EncryptedJsonStore(self._persist_path)
            active = {
                url: m.to_dict() for url, m in self._monitors.items() if m.active
            }
            store.save(active)
        except Exception:
            log.debug("Could not save page monitors", exc_info=True)

    def load(self) -> None:
        """Load monitors from disk (does NOT start periodic tasks)."""
        try:
            store = EncryptedJsonStore(self._persist_path)
            data = store.load(default={})
            for url, entry_data in data.items():
                self._monitors[url] = _MonitorEntry(
                    url=entry_data["url"],
                    interval_minutes=entry_data.get("interval_minutes", 30),
                    last_text=entry_data.get("last_text", ""),
                    last_hash=entry_data.get("last_hash", ""),
                    last_checked=entry_data.get("last_checked", 0.0),
                )
        except Exception:
            log.debug("Could not load page monitors", exc_info=True)

    # -- public API --------------------------------------------------------

    def start_monitoring(
        self,
        url: str,
        interval_minutes: float = 30,
    ) -> dict[str, Any]:
        if url in self._monitors and self._monitors[url].active:
            return {"success": False, "error": f"Already monitoring {url}"}

        # Take initial snapshot synchronously.
        text, err = _fetch_page_text(url)
        if err:
            return {"success": False, "error": err}

        entry = _MonitorEntry(
            url=url,
            interval_minutes=interval_minutes,
            last_text=text,
            last_hash=_text_hash(text),
            last_checked=time.time(),
        )
        self._monitors[url] = entry
        self._save()

        # Start periodic check task if there is a running event loop.
        self._start_task(entry)

        return {
            "success": True,
            "url": url,
            "interval_minutes": interval_minutes,
            "snapshot_chars": len(text),
        }

    def stop_monitoring(self, url: str) -> dict[str, Any]:
        entry = self._monitors.get(url)
        if not entry or not entry.active:
            return {"success": False, "error": f"Not monitoring {url}"}
        entry.active = False
        if entry.task and not entry.task.done():
            entry.task.cancel()
        self._monitors.pop(url, None)
        self._save()
        return {"success": True, "url": url}

    def list_monitors(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for url, entry in self._monitors.items():
            if not entry.active:
                continue
            result.append({
                "url": url,
                "interval_minutes": entry.interval_minutes,
                "last_checked": entry.last_checked,
                "snapshot_chars": len(entry.last_text),
            })
        return result

    def check_now(self, url: str) -> dict[str, Any]:
        """Check a monitored page immediately.  Returns diff if changed."""
        entry = self._monitors.get(url)
        if not entry or not entry.active:
            return {"success": False, "error": f"Not monitoring {url}"}

        new_text, err = _fetch_page_text(url)
        if err:
            return {"success": False, "error": err}

        new_hash = _text_hash(new_text)
        entry.last_checked = time.time()

        if new_hash == entry.last_hash:
            self._save()
            return {"success": True, "changed": False, "url": url}

        diff = _compute_diff(entry.last_text, new_text)
        entry.last_text = new_text
        entry.last_hash = new_hash
        self._save()

        if self._on_change:
            try:
                self._on_change(url, diff)
            except Exception:
                log.debug("on_change callback failed", exc_info=True)

        return {"success": True, "changed": True, "url": url, "diff": diff}

    # -- periodic task -----------------------------------------------------

    def _start_task(self, entry: _MonitorEntry) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop — test scenario

        async def _periodic() -> None:
            while entry.active:
                await asyncio.sleep(entry.interval_minutes * 60)
                if not entry.active:
                    break
                try:
                    self.check_now(entry.url)
                except Exception:
                    log.debug("Periodic check failed for %s", entry.url, exc_info=True)

        entry.task = loop.create_task(_periodic())


# ---------------------------------------------------------------------------
# Singleton store
# ---------------------------------------------------------------------------

_store: MonitorStore | None = None


def _get_store() -> MonitorStore:
    global _store
    if _store is None:
        _store = MonitorStore()
        _store.load()
    return _store


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class StartMonitorArgs(BaseModel):
    url: str = Field(description="URL of the page to monitor.")
    interval_minutes: float = Field(
        default=30,
        ge=1,
        le=1440,
        description="Check interval in minutes (1–1440, default 30).",
    )


class StopMonitorArgs(BaseModel):
    url: str = Field(description="URL to stop monitoring.")


class CheckPageArgs(BaseModel):
    url: str = Field(description="URL to check for changes right now.")


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

class StartPageMonitorTool:
    """Start monitoring a web page for changes."""

    name: str = "start_page_monitor"
    description: str = (
        "Starts monitoring a web page for changes. Saves a text snapshot and "
        "periodically checks for differences. "
        "(RU: следи за страницей, отслеживай страницу / "
        "UK: стеж за сторінкою, відстежуй сторінку)"
    )
    args_schema = StartMonitorArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^monitor\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^watch\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^track\s+page\s+changes$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^stop\s+monitoring$",
            priority=460,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^следи\s+за\s+страниц[еёой]+$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^отслеживай\s+страниц[уа]$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^что\s+изменилось\s+на\s+страниц[еу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^перестань\s+следить$",
            priority=460,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^стеж\s+за\s+сторінк[оюа]$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^відстежуй\s+сторінк[уа]$",
            priority=460,
            args=lambda m: {"url": ""},
        ),
        VoicePattern(
            regex=r"^що\s+змінилось\s+на\s+сторінц[іу]$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^перестань\s+стежити$",
            priority=460,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, store: MonitorStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> MonitorStore:
        return self._store or _get_store()

    async def execute(self, args: StartMonitorArgs) -> ToolResult:
        store = self._get_store()
        result = await asyncio.to_thread(
            store.start_monitoring, args.url, args.interval_minutes,
        )
        if result.get("success"):
            return ToolResult(
                success=True,
                output=(
                    f"Now monitoring {args.url} every "
                    f"{args.interval_minutes:.0f} minutes "
                    f"(snapshot: {result['snapshot_chars']} chars), sir."
                ),
            )
        return ToolResult(success=False, error=result.get("error", "Failed to start monitoring."))


class StopPageMonitorTool:
    """Stop monitoring a web page."""

    name: str = "stop_page_monitor"
    description: str = (
        "Stops monitoring a web page. "
        "(RU: перестань следить за страницей / "
        "UK: перестань стежити за сторінкою)"
    )
    args_schema = StopMonitorArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = ()

    def __init__(self, *, store: MonitorStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> MonitorStore:
        return self._store or _get_store()

    async def execute(self, args: StopMonitorArgs) -> ToolResult:
        store = self._get_store()
        result = store.stop_monitoring(args.url)
        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Stopped monitoring {args.url}, sir.",
            )
        return ToolResult(success=False, error=result.get("error", "Not monitoring that URL."))


class CheckPageChangesTool:
    """Check a monitored page for changes immediately."""

    name: str = "check_page_changes"
    description: str = (
        "Checks a monitored page for changes right now and returns a diff. "
        "(RU: что изменилось на странице / UK: що змінилось на сторінці)"
    )
    args_schema = CheckPageArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = ()

    def __init__(self, *, store: MonitorStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> MonitorStore:
        return self._store or _get_store()

    async def execute(self, args: CheckPageArgs) -> ToolResult:
        store = self._get_store()
        result = await asyncio.to_thread(store.check_now, args.url)
        if not result.get("success"):
            return ToolResult(success=False, error=result.get("error", "Check failed."))
        if not result.get("changed"):
            return ToolResult(
                success=True,
                output=f"No changes detected on {args.url}, sir.",
            )
        diff = result.get("diff", "")
        preview = diff[:1000] + ("…" if len(diff) > 1000 else "")
        return ToolResult(
            success=True,
            output=f"Changes detected on {args.url}:\n{preview}",
        )


class ListPageMonitorsTool:
    """List all actively monitored pages."""

    name: str = "list_page_monitors"
    description: str = (
        "Lists all pages currently being monitored for changes. "
        "(RU: какие страницы отслеживаются / UK: які сторінки відстежуються)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = ()

    def __init__(self, *, store: MonitorStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> MonitorStore:
        return self._store or _get_store()

    async def execute(self, args: EmptyArgs) -> ToolResult:
        store = self._get_store()
        monitors = store.list_monitors()
        if not monitors:
            return ToolResult(success=True, output="No pages being monitored, sir.")
        lines = [f"{len(monitors)} monitored page(s):"]
        for m in monitors:
            lines.append(
                f"  • {m['url']} — every {m['interval_minutes']:.0f}min "
                f"({m['snapshot_chars']} chars)"
            )
        return ToolResult(success=True, output="\n".join(lines))
