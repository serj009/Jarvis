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
