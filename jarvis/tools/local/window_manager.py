"""Window management — focus, minimize, maximize, resize, snap, list.

Uses pygetwindow (with pywin32/ctypes fallback) for window operations.
Resolves app names via app_aliases for natural voice control.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


def _get_pygetwindow():
    """Lazy import pygetwindow."""
    try:
        import pygetwindow as gw
        return gw
    except ImportError:
        return None


def _get_win32gui():
    """Lazy import win32gui (pywin32)."""
    try:
        import win32gui
        return win32gui
    except ImportError:
        return None


def _get_ctypes_user32():
    """Fallback: ctypes for basic win32 operations."""
    try:
        import ctypes
        return ctypes.windll.user32
    except (ImportError, AttributeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------

def _list_windows_impl() -> list[dict]:
    """List all visible windows with titles."""
    gw = _get_pygetwindow()
    if gw is not None:
        windows = []
        for w in gw.getAllWindows():
            if w.title and w.visible:
                windows.append({
                    "title": w.title,
                    "hwnd": w._hWnd,
                    "minimized": w.isMinimized,
                    "maximized": w.isMaximized,
                    "active": w.isActive,
                })
        return windows

    win32gui = _get_win32gui()
    if win32gui is not None:
        windows = []
        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    windows.append({"title": title, "hwnd": hwnd})
        win32gui.EnumWindows(callback, None)
        return windows

    raise NotImplementedError(
        "Window management requires pygetwindow or pywin32. "
        "Install: pip install pygetwindow"
    )


def _find_window(title_part: str):
    """Find a pygetwindow Window by partial title match."""
    gw = _get_pygetwindow()
    if gw is None:
        return None
    title_lower = title_part.lower()
    for w in gw.getAllWindows():
        if w.title and title_lower in w.title.lower():
            return w
    return None


def _resolve_app_to_title(name: str) -> str:
    """Resolve an app alias to a window title search term."""
    from jarvis.tools.local.app_aliases import APP_ALIASES
    canonical = APP_ALIASES.get(name.lower(), name)
    return canonical


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class WindowActionArgs(BaseModel):
    action: Literal[
        "focus", "minimize", "maximize", "restore",
        "snap_left", "snap_right", "close",
    ] = Field(description="Window action to perform.")
    window: str = Field(
        description="Window/app name or partial title to match."
    )


class ListWindowsArgs(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class WindowActionTool:
    """Perform an action on a specific window."""

    name: str = "window_action"
    description: str = (
        "Manage a desktop window: focus, minimize, maximize, restore, "
        "snap left/right, or close. Pass the app name or window title. "
        "(RU: окно, сверни, разверни, фокус / UK: вікно, згорни, розгорни, фокус)"
    )
    args_schema = WindowActionArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "minimize chrome", "maximize discord", "focus youtube"
        VoicePattern(
            regex=r"^(minimize|maximise|maximize|focus|restore)\s+(.+)$",
            priority=200,
            args=lambda m: {
                "action": "maximize" if m.group(1).startswith("maximi") else m.group(1),
                "window": m.group(2),
            },
        ),
        VoicePattern(
            regex=r"^snap\s+(.+)\s+(left|right)$",
            priority=200,
            args=lambda m: {"action": f"snap_{m.group(2)}", "window": m.group(1)},
        ),
        # RU: "сверни хром", "разверни дискорд", "фокус на ютуб"
        VoicePattern(
            regex=r"^(сверни|разверни|фокус(?:\s+на)?)\s+(.+)$",
            priority=200,
            args=lambda m: {
                "action": {
                    "сверни": "minimize",
                    "разверни": "maximize",
                }.get(m.group(1), "focus"),
                "window": m.group(2),
            },
        ),
        # UK: "згорни хром", "розгорни діскорд", "фокус на ютуб"
        VoicePattern(
            regex=r"^(згорни|розгорни|фокус(?:\s+на)?)\s+(.+)$",
            priority=200,
            args=lambda m: {
                "action": {
                    "згорни": "minimize",
                    "розгорни": "maximize",
                }.get(m.group(1), "focus"),
                "window": m.group(2),
            },
        ),
    )

    async def execute(self, args: WindowActionArgs) -> ToolResult:
        title = _resolve_app_to_title(args.window)
        try:
            gw = _get_pygetwindow()
            if gw is None:
                return ToolResult(
                    success=False,
                    error="pygetwindow not installed. Install: pip install pygetwindow",
                )

            win = _find_window(title)
            if win is None:
                # Try the raw name too
                if title != args.window:
                    win = _find_window(args.window)
                if win is None:
                    return ToolResult(
                        success=False,
                        error=f"Window '{args.window}' not found, sir.",
                    )

            action = args.action
            await asyncio.to_thread(self._perform_action, win, action)

            action_past = {
                "focus": "Focused",
                "minimize": "Minimized",
                "maximize": "Maximized",
                "restore": "Restored",
                "snap_left": "Snapped left",
                "snap_right": "Snapped right",
                "close": "Closed",
            }.get(action, action.title())

            return ToolResult(
                success=True,
                output=f"{action_past} {win.title}, sir.",
            )
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("window_action failed for %r", args.window)
            return ToolResult(success=False, error=f"Window action failed: {e}")

    @staticmethod
    def _perform_action(win, action: str) -> None:
        """Execute the window action (runs in thread)."""
        if action == "focus":
            win.activate()
        elif action == "minimize":
            win.minimize()
        elif action == "maximize":
            win.maximize()
        elif action == "restore":
            win.restore()
        elif action == "snap_left":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
            except (ImportError, AttributeError, OSError):
                screen_w, screen_h = 1920, 1080
            win.restore()
            win.moveTo(0, 0)
            win.resizeTo(screen_w // 2, screen_h)
        elif action == "snap_right":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
            except (ImportError, AttributeError, OSError):
                screen_w, screen_h = 1920, 1080
            win.restore()
            win.moveTo(screen_w // 2, 0)
            win.resizeTo(screen_w // 2, screen_h)
        elif action == "close":
            win.close()


class ListWindowsTool:
    """List all visible windows."""

    name: str = "list_windows"
    description: str = (
        "Lists all currently visible desktop windows with titles and state. "
        "(RU: список окон, какие окна открыты / UK: список вікон, які вікна відкриті)"
    )
    args_schema = ListWindowsArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^list\s+(?:all\s+)?windows$", priority=200),
        VoicePattern(regex=r"^(?:список|покажи)\s+окон$", priority=200),
        VoicePattern(regex=r"^(?:список|покажи)\s+вікон$", priority=200),
        VoicePattern(
            regex=r"^(?:какие|які)\s+окна\s+открыт[ыо]$", priority=200
        ),
    )

    async def execute(self, args: ListWindowsArgs) -> ToolResult:
        try:
            windows = await asyncio.to_thread(_list_windows_impl)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to list windows: {e}")

        if not windows:
            return ToolResult(success=True, output="No visible windows found, sir.")

        lines = [f"Found {len(windows)} window(s), sir:"]
        for w in windows[:25]:
            title = w.get("title", "Untitled")
            state = ""
            if w.get("minimized"):
                state = " [minimized]"
            elif w.get("maximized"):
                state = " [maximized]"
            elif w.get("active"):
                state = " [active]"
            lines.append(f"  • {title}{state}")
        if len(windows) > 25:
            lines.append(f"  … and {len(windows) - 25} more.")
        return ToolResult(success=True, output="\n".join(lines))
