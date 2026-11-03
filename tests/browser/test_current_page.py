"""jarvis.browser.current_page: which page is the user looking at.

The lookup order is the contract: Selenium session, then the user's own
browser (UI Automation), then the page Jarvis opened last. The conftest
fixture stubs the browser seam to None; tests here patch it back as needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.browser import current_page as cp


def _selenium(url: str = "https://selenium.example/page", title: str = "Sel",
              active: bool = True) -> MagicMock:
    bc = MagicMock()
    bc.driver_active = active
    bc.get_current_url.return_value = {"success": True, "url": url}
    bc.get_page_title.return_value = {"success": True, "title": title}
    return bc


class TestLastOpened:
    def test_empty_by_default(self):
        assert cp.last_opened_url() is None

    def test_records_and_returns(self):
        cp.note_opened_url("https://a.example/")
        assert cp.last_opened_url() == "https://a.example/"

    def test_expires(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(cp.time, "monotonic", lambda: now[0])
        cp.note_opened_url("https://a.example/")
        now[0] += cp.LAST_OPENED_MAX_AGE_S + 1
        assert cp.last_opened_url() is None

    def test_empty_url_ignored(self):
        cp.note_opened_url("")
        assert cp.last_opened_url() is None

    def test_reset(self):
        cp.note_opened_url("https://a.example/")
        cp.reset()
        assert cp.last_opened_url() is None


class TestLookupOrder:
    def test_nothing_known_returns_none(self):
        assert cp.current_page(None) is None

    def test_selenium_wins(self, monkeypatch):
        monkeypatch.setattr(cp, "foreground_browser_url", lambda: "https://browser.example/")
        cp.note_opened_url("https://opened.example/")
        page = cp.current_page(_selenium())
        assert page is not None
        assert page.source == "selenium"
        assert page.url == "https://selenium.example/page"
        assert page.title == "Sel"

    def test_inactive_selenium_falls_through_to_browser(self, monkeypatch):
        monkeypatch.setattr(cp, "foreground_browser_url", lambda: "https://browser.example/")
        page = cp.current_page(_selenium(active=False))
        assert page is not None
        assert (page.source, page.url) == ("browser", "https://browser.example/")

    def test_browser_beats_last_opened(self, monkeypatch):
        # The user may have navigated away from what Jarvis opened.
        monkeypatch.setattr(cp, "foreground_browser_url", lambda: "https://browser.example/")
        cp.note_opened_url("https://opened.example/")
        page = cp.current_page(None)
        assert page is not None
        assert page.source == "browser"

    def test_last_opened_when_browser_unreadable(self):
        cp.note_opened_url("https://opened.example/")
        page = cp.current_page(None)
        assert page is not None
        assert (page.source, page.url) == ("last_opened", "https://opened.example/")

    def test_browser_lookup_error_is_not_fatal(self, monkeypatch):
        def boom() -> str:
            raise RuntimeError("uia exploded")

        monkeypatch.setattr(cp, "foreground_browser_url", boom)
        cp.note_opened_url("https://opened.example/")
        page = cp.current_page(None)
        assert page is not None
        assert page.source == "last_opened"

    def test_selenium_error_is_not_fatal(self):
        bc = MagicMock()
        bc.driver_active = True
        bc.get_current_url.side_effect = RuntimeError("driver died")
        cp.note_opened_url("https://opened.example/")
        page = cp.current_page(bc)
        assert page is not None
        assert page.source == "last_opened"


class TestOpenUrlRecordsPage:
    @pytest.mark.asyncio
    async def test_open_url_notes_the_page(self, monkeypatch):
        from jarvis.platform import windows as winplat
        from jarvis.tools.local.open_url import OpenUrlArgs, OpenUrlTool

        monkeypatch.setattr(winplat, "open_url", lambda url: None)
        monkeypatch.setattr(
            "jarvis.tools.local.open_url.youtube_watch_url_from_url", lambda url: None
        )
        result = await OpenUrlTool().execute(OpenUrlArgs(url="https://www.allrecipes.com/x"))
        assert result.success
        assert cp.last_opened_url() == "https://www.allrecipes.com/x"

    @pytest.mark.asyncio
    async def test_rejected_url_is_not_noted(self):
        from jarvis.tools.local.open_url import OpenUrlArgs, OpenUrlTool

        result = await OpenUrlTool().execute(OpenUrlArgs(url="file:///c:/secret.txt"))
        assert not result.success
        assert cp.last_opened_url() is None
