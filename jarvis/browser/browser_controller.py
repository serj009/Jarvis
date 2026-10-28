"""Browser controller — two-mode browser management.

Simple mode (always available):
    Uses ``webbrowser.open()`` for URL opening.  No control after launch.

Advanced mode (optional, Selenium):
    Full tab management, navigation, DOM access.  Lazy-initialised only when
    a caller actually needs it and ``selenium`` is importable.

    Install:  ``pip install jarvis[browser]``

Simple mode ALWAYS works.  Advanced mode is opt-in and degrades gracefully.
"""

from __future__ import annotations

import logging
import webbrowser
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Selenium imports
# ---------------------------------------------------------------------------


def _try_import_selenium() -> bool:
    """Return True if selenium is importable."""
    try:
        import selenium  # noqa: F401
        return True
    except ImportError:
        return False


class BrowserController:
    """Browser navigation and tab management.

    Parameters
    ----------
    mode:
        ``"simple"`` — webbrowser only, no Selenium attempt.
        ``"selenium"`` — require Selenium, fail if absent.
        ``"auto"`` (default) — try Selenium, fall back to simple.
    browser:
        WebDriver flavour: ``"chrome"`` | ``"firefox"`` | ``"edge"``.
    headless:
        Launch invisible (scraping / CI).
    """

    def __init__(
        self,
        mode: str = "auto",
        browser: str = "chrome",
        *,
        headless: bool = False,
    ) -> None:
        self._mode = mode
        self._browser_name = browser
        self._headless = headless
        self._driver: Any = None  # selenium.webdriver.Remote | None
        self._selenium_available: bool = False
        self._options: Any = None

        if mode in ("selenium", "auto"):
            self._probe_selenium()

    # -- Selenium lifecycle -------------------------------------------

    def _probe_selenium(self) -> None:
        """Check whether Selenium is importable and prepare options."""
        if not _try_import_selenium():
            log.info(
                "Selenium not installed — browser stays in simple mode. "
                "Install: pip install selenium"
            )
            return

        try:
            from selenium.webdriver.chrome.options import Options

            options = Options()
            if self._headless:
                options.add_argument("--headless=new")
            self._options = options
            self._selenium_available = True
            log.info("Selenium available (%s)", self._browser_name)
        except Exception:
            log.debug("Selenium import succeeded but Options init failed", exc_info=True)

    def _ensure_driver(self) -> bool:
        """Lazily start the WebDriver.  Returns True on success."""
        if self._driver is not None:
            return True
        if not self._selenium_available:
            return False
        try:
            from selenium import webdriver

            self._driver = webdriver.Chrome(options=self._options)
            log.info("Chrome WebDriver started")
            return True
        except Exception:
            log.warning("Failed to start WebDriver", exc_info=True)
            return False

    @property
    def has_advanced(self) -> bool:
        """Whether Selenium-backed operations are available."""
        return self._selenium_available

    @property
    def driver_active(self) -> bool:
        """Whether a WebDriver session is currently open."""
        return self._driver is not None

    # -- Navigation ---------------------------------------------------

    def open_tab(self, url: str) -> dict[str, Any]:
        """Open *url* in a new browser tab.

        Advanced mode: Selenium ``switch_to.new_window`` + ``get`` (no JS
        string interpolation — the raw URL is passed as data, not code).
        Simple fallback: ``webbrowser.open_new_tab``.
        """
        try:
            url = _normalise_url(url)
        except UnsafeUrlError as exc:
            return {"success": False, "error": str(exc)}

        if self._ensure_driver():
            try:
                # Open a real new tab and navigate to the URL as data. Avoids
                # building a window.open("<url>") JS string, which a crafted
                # URL could break out of.
                self._driver.switch_to.new_window("tab")
                self._driver.get(url)
                return {
                    "success": True,
                    "url": url,
                    "tab_count": len(self._driver.window_handles),
                    "mode": "selenium",
                }
            except Exception as exc:
                log.warning("Selenium open_tab failed: %s", exc)

        # Simple fallback
        webbrowser.open_new_tab(url)
        return {"success": True, "url": url, "mode": "simple"}

    def close_tab(self) -> dict[str, Any]:
        """Close the current Selenium tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control (Selenium not active)"}
        try:
            self._driver.close()
            handles = self._driver.window_handles
            if handles:
                self._driver.switch_to.window(handles[-1])
            else:
                self._driver = None
            return {"success": True, "remaining_tabs": len(handles)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def switch_tab(self, index: int) -> dict[str, Any]:
        """Switch to a tab by *index* (0-based)."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        handles = self._driver.window_handles
        if 0 <= index < len(handles):
            self._driver.switch_to.window(handles[index])
            return {"success": True, "tab": index, "title": self._driver.title}
        return {
            "success": False,
            "error": f"Tab {index} not found. {len(handles)} tab(s) open.",
        }

    def list_tabs(self) -> dict[str, Any]:
        """Return info about every open tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        current = self._driver.current_window_handle
        tabs: list[dict[str, Any]] = []
        for i, handle in enumerate(self._driver.window_handles):
            self._driver.switch_to.window(handle)
            tabs.append({
                "index": i,
                "title": self._driver.title,
                "url": self._driver.current_url,
                "active": handle == current,
            })
        self._driver.switch_to.window(current)
        return {"success": True, "tabs": tabs, "count": len(tabs)}

    # -- History navigation -------------------------------------------

    def go_back(self) -> dict[str, Any]:
        """Navigate back in the current tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        self._driver.back()
        return {"success": True, "action": "back"}

    def go_forward(self) -> dict[str, Any]:
        """Navigate forward in the current tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        self._driver.forward()
        return {"success": True, "action": "forward"}

    def refresh(self) -> dict[str, Any]:
        """Refresh the current tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        self._driver.refresh()
        return {"success": True, "action": "refresh"}

    # -- Info ---------------------------------------------------------

    def get_current_url(self) -> dict[str, Any]:
        """Return the URL of the active tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        return {"success": True, "url": self._driver.current_url}

    def get_page_title(self) -> dict[str, Any]:
        """Return the title of the active tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        return {"success": True, "title": self._driver.title}

    # -- Content (Selenium-only) --------------------------------------

    def get_page_text(self, max_chars: int = 5000) -> dict[str, Any]:
        """Extract visible body text from the active tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        try:
            text = self._driver.find_element("tag name", "body").text
            # Security Layer 3: page text is untrusted and flows into the LLM
            # context — strip prompt-injection payloads before returning it.
            from jarvis.security.output_sanitizer import clean_external_text

            text = clean_external_text(text, source="browser:page")
            truncated = len(text) > max_chars
            return {
                "success": True,
                "text": text[:max_chars],
                "length": len(text),
                "truncated": truncated,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_page_source(self) -> dict[str, Any]:
        """Return the raw HTML source of the active tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        try:
            # Same injection risk as get_page_text: raw HTML reaches the LLM
            # via callers (e.g. image_scraper), so sanitise it first.
            from jarvis.security.output_sanitizer import clean_external_text

            html = clean_external_text(self._driver.page_source, source="browser:page")
            return {"success": True, "html": html}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # -- Media (Selenium JS injection) --------------------------------

    def execute_js(self, script: str) -> dict[str, Any]:
        """Run arbitrary JavaScript in the active tab."""
        if not self._driver:
            return {"success": False, "error": "No browser control"}
        try:
            result = self._driver.execute_script(script)
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # -- Cleanup ------------------------------------------------------

    def quit(self) -> None:
        """Shut down the WebDriver (if active)."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


# -- helpers ----------------------------------------------------------


class UnsafeUrlError(ValueError):
    """Raised when a URL uses a scheme we refuse to open in a browser."""


# Only web schemes may be opened. javascript:/data:/file:/vbscript: can run
# code or read local files, so they are rejected outright (Task 5).
_ALLOWED_URL_SCHEMES = ("http://", "https://")
_BLOCKED_URL_SCHEMES = ("javascript:", "data:", "file:", "vbscript:", "about:")


def _normalise_url(url: str) -> str:
    """Normalise + validate a URL for browser navigation.
    Prepends ``https://`` when no scheme is given, and rejects dangerous
    schemes (javascript:/data:/file:/vbscript:/about:) with
    ``UnsafeUrlError``. Only http/https survive.
    """
    stripped = url.strip()
    lowered = stripped.lower()
    for bad in _BLOCKED_URL_SCHEMES:
        if lowered.startswith(bad):
            raise UnsafeUrlError(f"refusing to open {bad!r} URL")
    if not lowered.startswith(_ALLOWED_URL_SCHEMES):
        # No (or unknown) scheme → default to https. If it still contains a
        # scheme-like "word:" prefix that isn't http(s), reject it.
        scheme_sep = stripped.find(":")
        slash = stripped.find("/")
        if scheme_sep != -1 and (slash == -1 or scheme_sep < slash):
            raise UnsafeUrlError("refusing to open non-http(s) URL scheme")
        return "https://" + stripped
    return stripped
