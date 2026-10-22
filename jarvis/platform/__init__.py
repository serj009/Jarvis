"""OS-platform package: per-OS backends behind one dispatcher.

Each backend module (:mod:`~jarvis.platform.windows`,
:mod:`~jarvis.platform.darwin`, :mod:`~jarvis.platform.linux`) implements the
same function surface described by :class:`jarvis.platform.base.PlatformOps`.

:func:`get_platform` picks the right backend for the current host. Tools may
either import a backend directly (``from jarvis.platform import windows as
winplat`` — the established convention for Windows-only tools) or go through
the dispatcher helpers defined here (``from jarvis import platform as
plat; plat.list_startup()``) when they want the call to resolve per-OS.

Importing this package is always safe: the backend modules only touch
win32 / ctypes / winreg lazily inside their function bodies, and the macOS /
Linux stubs import nothing platform-specific at all.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Optional


def get_platform() -> ModuleType:
    """Return the backend module matching the current ``sys.platform``.

    ``win32`` -> :mod:`jarvis.platform.windows`;
    ``darwin`` -> :mod:`jarvis.platform.darwin`;
    anything else -> :mod:`jarvis.platform.linux`.
    """
    if sys.platform == "win32":
        from jarvis.platform import windows
        return windows
    if sys.platform == "darwin":
        from jarvis.platform import darwin
        return darwin
    from jarvis.platform import linux
    return linux


# -- dispatcher helpers -------------------------------------------------
#
# Thin pass-throughs so callers can do plat.list_startup() without first
# resolving the backend. Each simply forwards to the selected module, whose
# function raises NotImplementedError / NotSupportedError when unavailable.


def list_startup() -> list[dict]:
    return get_platform().list_startup()


def add_to_startup(name: str, command: str) -> str:
    return get_platform().add_to_startup(name, command)


def remove_from_startup(name: str) -> str:
    return get_platform().remove_from_startup(name)


def set_brightness(level: int) -> str:
    return get_platform().set_brightness(level)


def get_brightness() -> Optional[int]:
    return get_platform().get_brightness()


def toggle_night_mode(enable: bool) -> str:
    return get_platform().toggle_night_mode(enable)


def display_info() -> dict:
    return get_platform().display_info()


def list_windows_win32() -> list[dict]:
    return get_platform().list_windows_win32()


def get_screen_metrics() -> tuple[int, int]:
    return get_platform().get_screen_metrics()


def message_box(message: str, *, title: str = "Jarvis", flags: int = 0x10,
                hwnd: int = 0) -> int:
    return get_platform().message_box(
        message, title=title, flags=flags, hwnd=hwnd,
    )


def foreground_window_title() -> str:
    return get_platform().foreground_window_title()


def is_user_admin() -> bool:
    return get_platform().is_user_admin()


def sleep_system() -> None:
    return get_platform().sleep_system()


def hibernate_system() -> None:
    return get_platform().hibernate_system()


def prevent_sleep() -> None:
    return get_platform().prevent_sleep()


__all__ = [
    "add_to_startup",
    "display_info",
    "foreground_window_title",
    "get_brightness",
    "get_platform",
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
