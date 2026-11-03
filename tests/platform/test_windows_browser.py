"""open_url uses the user's default browser and raises its window.

The Win32 window calls are best-effort and need a real desktop, so they are
verified live; these tests cover the parts that can be checked offline."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from jarvis.platform import windows as winplat


@pytest.mark.parametrize(
    "command,expected",
    [
        (r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --single-argument %1',
         "chrome.exe"),
        (r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --single-argument %1',
         "msedge.exe"),
        (r'C:\Program Files\Mozilla Firefox\firefox.exe -osint -url "%1"', "firefox.exe"),
        (r'"C:\Users\x\AppData\Local\Programs\Opera\Launcher.EXE" "%1"', "launcher.exe"),
        (r'"C:\tools\open.bat" %1', None),
        ("", None),
    ],
)
def test_exe_from_open_command(command, expected):
    assert winplat._exe_from_open_command(command) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Windows default-browser path")
def test_open_url_uses_shell_default_and_raises_browser():
    with (
        patch.object(winplat.os, "startfile", create=True) as startfile,
        patch.object(winplat, "_start_browser_focus") as focus,
        patch.object(winplat.webbrowser, "open") as wb_open,
    ):
        winplat.open_url("https://github.com")

    startfile.assert_called_once_with("https://github.com")
    focus.assert_called_once_with()
    wb_open.assert_not_called()


def test_focus_never_raises_when_registry_lookup_fails():
    with patch.object(winplat, "default_browser_exe", side_effect=OSError("boom")):
        winplat._focus_default_browser()  # must not raise


# -- active_browser_url: address-bar text -> URL --------------------------
#
# The UI Automation call itself needs a real desktop and a running browser,
# so it is verified live (see the PR notes); these tests cover the parts that
# can be checked offline: text normalisation and the never-raise contract.


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://github.com/user/repo", "https://github.com/user/repo"),
        ("http://localhost:8080/x", "http://localhost:8080/x"),
        # Chrome/Edge hide the scheme in the address bar.
        ("github.com/user/repo", "https://github.com/user/repo"),
        ("www.allrecipes.com/recipe/1/borsch?x=1", "https://www.allrecipes.com/recipe/1/borsch?x=1"),
        ("ru.wikipedia.org", "https://ru.wikipedia.org"),
        ("localhost:3000", "https://localhost:3000"),
        ("  https://example.com  ", "https://example.com"),
        # Not a page we can fetch: typed search words, browser pages, empty.
        ("bleach reborn of souls гайд", None),
        ("рецепт борща", None),
        ("chrome://settings", None),
        ("edge://newtab", None),
        ("about:blank", None),
        ("file:///C:/x.html", None),
        ("ftp://example.com/x", None),
        ("", None),
        ("   ", None),
        ("justoneword", None),
    ],
)
def test_normalise_address_bar_text(text, expected):
    assert winplat.normalise_address_bar_text(text) == expected


def test_active_browser_url_is_none_off_windows(monkeypatch):
    monkeypatch.setattr(winplat.sys, "platform", "linux")
    assert winplat.active_browser_url() is None


@pytest.mark.parametrize("error", [ImportError("no comtypes"), OSError("uia"), RuntimeError("x")])
def test_active_browser_url_never_raises(monkeypatch, error):
    monkeypatch.setattr(winplat.sys, "platform", "win32")
    monkeypatch.setattr(winplat, "_user32", lambda: object())
    monkeypatch.setattr(winplat, "_browser_window", lambda user32: 1234)

    def fail(hwnd):
        raise error

    monkeypatch.setattr(winplat, "_uia_address_bar_text", fail)
    assert winplat.active_browser_url() is None


def test_active_browser_url_normalises_the_bar(monkeypatch):
    monkeypatch.setattr(winplat.sys, "platform", "win32")
    monkeypatch.setattr(winplat, "_user32", lambda: object())
    monkeypatch.setattr(winplat, "_browser_window", lambda user32: 1234)
    monkeypatch.setattr(winplat, "_uia_address_bar_text", lambda hwnd: "fextralife.com/bleach")
    assert winplat.active_browser_url() == "https://fextralife.com/bleach"


def test_active_browser_url_none_without_browser_window(monkeypatch):
    monkeypatch.setattr(winplat.sys, "platform", "win32")
    monkeypatch.setattr(winplat, "_user32", lambda: object())
    monkeypatch.setattr(winplat, "_browser_window", lambda user32: None)
    called = []
    monkeypatch.setattr(winplat, "_uia_address_bar_text", lambda hwnd: called.append(hwnd))
    assert winplat.active_browser_url() is None
    assert called == []
