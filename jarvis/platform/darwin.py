"""Stub platform backend for macOS.

Mirrors the function surface of :mod:`jarvis.platform.windows` for the
startup / display / window operations, but every function raises
:class:`~jarvis.platform.base.NotSupportedError` (a subclass of
``NotImplementedError``). The tool layer already handles
``NotImplementedError`` by returning a clean ``ToolResult`` error, so these
operations degrade gracefully on macOS.

This module must NOT import win32 / ctypes / winreg / PySide6.
"""

from __future__ import annotations


from jarvis.platform.base import NotSupportedError

_PLATFORM = "macOS"


# -- startup ------------------------------------------------------------


def list_startup() -> list[dict]:
    raise NotSupportedError.for_feature("list_startup", _PLATFORM)


def add_to_startup(name: str, command: str) -> str:
    raise NotSupportedError.for_feature("add_to_startup", _PLATFORM)


def remove_from_startup(name: str) -> str:
    raise NotSupportedError.for_feature("remove_from_startup", _PLATFORM)


# -- display ------------------------------------------------------------


def set_brightness(level: int) -> str:
    raise NotSupportedError.for_feature("set_brightness", _PLATFORM)


def get_brightness() -> int | None:
    raise NotSupportedError.for_feature("get_brightness", _PLATFORM)


def toggle_night_mode(enable: bool) -> str:
    raise NotSupportedError.for_feature("toggle_night_mode", _PLATFORM)


def display_info() -> dict:
    raise NotSupportedError.for_feature("display_info", _PLATFORM)


# -- window (low-level primitives) --------------------------------------


def list_windows_win32() -> list[dict]:
    raise NotSupportedError.for_feature("list_windows_win32", _PLATFORM)


def get_screen_metrics() -> tuple[int, int]:
    raise NotSupportedError.for_feature("get_screen_metrics", _PLATFORM)


# -- message box / foreground window / admin / power -------------------


def message_box(message: str, *, title: str = "Jarvis", flags: int = 0x10,
                hwnd: int = 0) -> int:
    raise NotSupportedError.for_feature("message_box", _PLATFORM)


def foreground_window_title() -> str:
    raise NotSupportedError.for_feature("foreground_window_title", _PLATFORM)


def is_user_admin() -> bool:
    raise NotSupportedError.for_feature("is_user_admin", _PLATFORM)


def sleep_system() -> None:
    raise NotSupportedError.for_feature("sleep_system", _PLATFORM)


def hibernate_system() -> None:
    raise NotSupportedError.for_feature("hibernate_system", _PLATFORM)


def prevent_sleep() -> None:
    raise NotSupportedError.for_feature("prevent_sleep", _PLATFORM)


__all__ = [
    "add_to_startup",
    "display_info",
    "foreground_window_title",
    "get_brightness",
    "get_screen_metrics",
    "hibernate_system",
    "is_user_admin",
    "list_startup",
    "list_windows_win32",
    "message_box",
    "prevent_sleep",
    "remove_from_startup",
    "set_brightness",
    "sleep_system",
    "toggle_night_mode",
]
