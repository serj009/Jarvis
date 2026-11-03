"""Which page is the user looking at right now?

One answer for every "current page" tool -- save_page_knowledge, bookmarks,
content_reader -- so they cannot disagree, and so "remember this page" works
for the browser the user actually uses, not only for a Selenium-driven one.

Lookup order (first hit wins):

1. ``selenium``  -- Jarvis' own WebDriver session, if one is running. It is
   the only source that can also return the live DOM text.
2. ``browser``   -- the address bar of the user's own browser window, read
   read-only through UI Automation (``jarvis.platform.windows``).
3. ``last_opened`` -- the URL Jarvis itself opened with open_url during this
   session, if it is recent. Covers a browser whose address bar UIA cannot
   read (old Chrome, comtypes missing) in the most common flow:
   "open <site>" ... "remember this page".

The explicit URL a caller already has always beats all three; callers check
that themselves before asking here.

Why "last_opened" is in-memory only: it describes this session's browsing,
and persisting a browsing trail to disk is not something the user asked for.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# How long an open_url result stays a plausible "current page". After half
# an hour the user has almost certainly moved on, and a stale guess would
# silently save the wrong page.
LAST_OPENED_MAX_AGE_S = 30 * 60

_lock = threading.Lock()
_last_opened: tuple[str, float] | None = None


@dataclass(frozen=True)
class CurrentPage:
    url: str
    source: str  # "selenium" | "browser" | "last_opened"
    title: str = ""


def note_opened_url(url: str) -> None:
    """Record a URL Jarvis just opened in the user's browser (open_url)."""
    global _last_opened
    if not url:
        return
    with _lock:
        _last_opened = (url, time.monotonic())


def last_opened_url(max_age_s: float = LAST_OPENED_MAX_AGE_S) -> str | None:
    """The URL recorded by note_opened_url, if it is not older than max_age_s."""
    with _lock:
        entry = _last_opened
    if entry is None:
        return None
    url, opened_at = entry
    if time.monotonic() - opened_at > max_age_s:
        return None
    return url


def reset() -> None:
    """Forget the last opened URL (tests, and a future 'forget' command)."""
    global _last_opened
    with _lock:
        _last_opened = None


def foreground_browser_url() -> str | None:
    """Address of the page in the user's own browser (UIA), or None.

    Single seam for tests: the test suite patches this to None so no test
    ever reads the developer's real browser."""
    from jarvis.platform import windows as winplat

    return winplat.active_browser_url()


def _from_selenium(browser_controller: Any) -> CurrentPage | None:
    if browser_controller is None:
        return None
    try:
        if not browser_controller.driver_active:
            return None
        info = browser_controller.get_current_url()
        url = info.get("url", "") if info.get("success") else ""
        if not url:
            return None
        title_info = browser_controller.get_page_title()
        title = title_info.get("title", "") if title_info.get("success") else ""
        return CurrentPage(url=url, source="selenium", title=title)
    except Exception:
        log.debug("[current_page] selenium lookup failed", exc_info=True)
        return None


def current_page(browser_controller: Any = None) -> CurrentPage | None:
    """The page the user is looking at, or None if it cannot be determined.

    Blocking (UIA is a cross-process COM call): call it via asyncio.to_thread
    from async code."""
    page = _from_selenium(browser_controller)
    if page is not None:
        return page

    try:
        url = foreground_browser_url()
    except Exception:
        log.debug("[current_page] browser lookup failed", exc_info=True)
        url = None
    if url:
        log.info("[current_page] using the browser's address bar")
        return CurrentPage(url=url, source="browser")

    url = last_opened_url()
    if url:
        log.info("[current_page] using the page Jarvis opened last")
        return CurrentPage(url=url, source="last_opened")

    log.info("[current_page] no current page: no Selenium, no browser URL, nothing opened")
    return None
