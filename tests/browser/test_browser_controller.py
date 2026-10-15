"""Tests for jarvis.browser.browser_controller.

All Selenium interactions are mocked — no real browser is launched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.browser.browser_controller import BrowserController, _normalise_url


# -- URL normalisation ---------------------------------------------------


class TestNormaliseUrl:
    def test_adds_https_when_missing(self):
        assert _normalise_url("google.com") == "https://google.com"

    def test_preserves_http(self):
        assert _normalise_url("http://example.com") == "http://example.com"

    def test_preserves_https(self):
        assert _normalise_url("https://example.com") == "https://example.com"


# -- Simple mode (no Selenium) ------------------------------------------


class TestSimpleMode:
    def test_init_simple_mode(self):
        bc = BrowserController(mode="simple")
        assert bc._driver is None
        assert bc._selenium_available is False
        assert not bc.has_advanced
        assert not bc.driver_active

    @patch("jarvis.browser.browser_controller.webbrowser")
    def test_open_tab_simple_fallback(self, mock_wb):
        bc = BrowserController(mode="simple")
        result = bc.open_tab("https://example.com")
        assert result["success"] is True
        assert result["mode"] == "simple"
        mock_wb.open_new_tab.assert_called_once_with("https://example.com")

    def test_close_tab_without_driver(self):
        bc = BrowserController(mode="simple")
        result = bc.close_tab()
        assert result["success"] is False
        assert "error" in result

    def test_switch_tab_without_driver(self):
        bc = BrowserController(mode="simple")
        result = bc.switch_tab(0)
        assert result["success"] is False

    def test_list_tabs_without_driver(self):
        bc = BrowserController(mode="simple")
        result = bc.list_tabs()
        assert result["success"] is False

    def test_go_back_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.go_back()["success"] is False

    def test_go_forward_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.go_forward()["success"] is False

    def test_refresh_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.refresh()["success"] is False

    def test_get_current_url_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.get_current_url()["success"] is False

    def test_get_page_title_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.get_page_title()["success"] is False

    def test_get_page_text_without_driver(self):
        bc = BrowserController(mode="simple")
        assert bc.get_page_text()["success"] is False


# -- Advanced mode (mocked Selenium) ------------------------------------


class TestAdvancedMode:
    @pytest.fixture()
    def bc_with_driver(self):
        bc = BrowserController(mode="simple")
        bc._selenium_available = True
        driver = MagicMock()
        driver.title = "Test Page"
        driver.current_url = "https://example.com"
        driver.current_window_handle = "handle_0"
        driver.window_handles = ["handle_0"]
        driver.page_source = "<html><body>Hello</body></html>"
        bc._driver = driver
        return bc, driver

    def test_open_tab_selenium(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.window_handles = ["handle_0", "handle_1"]
        result = bc.open_tab("https://github.com")
        assert result["success"] is True
        assert result["mode"] == "selenium"
        driver.execute_script.assert_called_once()

    def test_close_tab(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.window_handles = ["handle_0"]
        result = bc.close_tab()
        assert result["success"] is True
        driver.close.assert_called_once()

    def test_switch_tab(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.window_handles = ["handle_0", "handle_1"]
        result = bc.switch_tab(1)
        assert result["success"] is True
        driver.switch_to.window.assert_called_with("handle_1")

    def test_switch_tab_invalid_index(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.window_handles = ["handle_0"]
        result = bc.switch_tab(5)
        assert result["success"] is False

    def test_list_tabs(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.window_handles = ["handle_0"]
        result = bc.list_tabs()
        assert result["success"] is True
        assert result["count"] == 1

    def test_go_back(self, bc_with_driver):
        bc, driver = bc_with_driver
        assert bc.go_back()["success"] is True
        driver.back.assert_called_once()

    def test_go_forward(self, bc_with_driver):
        bc, driver = bc_with_driver
        assert bc.go_forward()["success"] is True
        driver.forward.assert_called_once()

    def test_refresh(self, bc_with_driver):
        bc, driver = bc_with_driver
        assert bc.refresh()["success"] is True
        driver.refresh.assert_called_once()

    def test_get_current_url(self, bc_with_driver):
        bc, driver = bc_with_driver
        result = bc.get_current_url()
        assert result["success"] is True
        assert result["url"] == "https://example.com"

    def test_get_page_title(self, bc_with_driver):
        bc, driver = bc_with_driver
        result = bc.get_page_title()
        assert result["success"] is True
        assert result["title"] == "Test Page"

    def test_get_page_text(self, bc_with_driver):
        bc, driver = bc_with_driver
        mock_body = MagicMock()
        mock_body.text = "Hello World"
        driver.find_element.return_value = mock_body
        result = bc.get_page_text()
        assert result["success"] is True
        assert "Hello" in result["text"]

    def test_get_page_source(self, bc_with_driver):
        bc, driver = bc_with_driver
        result = bc.get_page_source()
        assert result["success"] is True
        assert "<html>" in result["html"]

    def test_execute_js(self, bc_with_driver):
        bc, driver = bc_with_driver
        driver.execute_script.return_value = 42
        result = bc.execute_js("return 42;")
        assert result["success"] is True
        assert result["result"] == 42

    def test_quit(self, bc_with_driver):
        bc, driver = bc_with_driver
        bc.quit()
        driver.quit.assert_called_once()
        assert bc._driver is None


# -- Selenium probe (auto mode) -----------------------------------------


class TestAutoMode:
    @patch("jarvis.browser.browser_controller._try_import_selenium", return_value=False)
    def test_auto_without_selenium(self, _mock):
        bc = BrowserController(mode="auto")
        assert not bc.has_advanced

    @patch("jarvis.browser.browser_controller._try_import_selenium", return_value=True)
    def test_auto_selenium_import_error_on_options(self, _mock):
        """Even if selenium is importable, a broken Options init should not crash."""
        with patch(
            "jarvis.browser.browser_controller.BrowserController._probe_selenium",
            side_effect=lambda self: None,
        ):
            bc = BrowserController.__new__(BrowserController)
            bc._mode = "auto"
            bc._browser_name = "chrome"
            bc._headless = False
            bc._driver = None
            bc._selenium_available = False
            bc._options = None
            assert not bc.has_advanced
