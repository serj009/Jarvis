"""Window management — focus, minimize, maximize, resize, snap, list.

Uses pygetwindow (cross-platform, so it stays here) for window operations,
falling back to the low-level Win32 primitives in platform/windows.py when
pygetwindow is unavailable. The win32gui / ctypes access lives behind the
platform seam (ARCHITECTURE RULE, SPEC.md line 75: no win32 imports outside
platform/windows.py). Resolves app names via app_aliases for natural voice
control.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis import platform as platform_ops
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


def _get_pygetwindow():
    """Lazy import pygetwindow."""
    try:
        import pygetwindow as gw
        return gw
    except ImportError:
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

    # Fallback: low-level Win32 enumeration behind the platform seam.
    # platform.windows.list_windows_win32 raises ImportError when pywin32 is
    # missing and NotImplementedError off Windows.
    try:
        return platform_ops.list_windows_win32()
    except ImportError:
        raise NotImplementedError(
            "Window management requires pygetwindow or pywin32. "
            "Install: pip install pygetwindow"
        ) from None


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


def _send_keys(*keys: str) -> bool:
    """Send a global hotkey via pyautogui. Returns False if unavailable.

    Used for shell-level actions that have no per-window handle: minimize
    all (Win+D), virtual-desktop switch (Win+Ctrl+Left/Right), Task View
    (Win+Tab).
    """
    try:
        import pyautogui
    except ImportError:
        return False
    try:
        pyautogui.hotkey(*keys)
        return True
    except Exception:
        log.warning("send_keys failed for %s", keys, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class WindowActionArgs(BaseModel):
    action: Literal[
        "focus", "minimize", "maximize", "restore",
        "snap_left", "snap_right",
        "snap_top_left", "snap_top_right",
        "snap_bottom_left", "snap_bottom_right",
        "snap_center", "close",
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
        # EN corners + center: "snap chrome top left", "center discord"
        VoicePattern(
            regex=r"^snap\s+(.+)\s+(top[\s-]?left|top[\s-]?right|bottom[\s-]?left|bottom[\s-]?right)$",
            priority=190,
            args=lambda m: {
                "action": "snap_" + m.group(2).replace("-", "_").replace(" ", "_"),
                "window": m.group(1),
            },
        ),
        # Center must use an explicit "center/centre" word and sit at a
        # HIGHER priority number than snap left/right/corners so it never
        # shadows them (first-match-wins = lower number wins).
        VoicePattern(
            regex=r"^(?:center|centre)\s+(.+)$",
            priority=210,
            args=lambda m: {"action": "snap_center", "window": m.group(1)},
        ),
        VoicePattern(
            regex=r"^snap\s+(.+?)\s+(?:to\s+)?(?:the\s+)?cent(?:er|re)$",
            priority=210,
            args=lambda m: {"action": "snap_center", "window": m.group(1)},
        ),
        # RU corners + center: "сверни хром вверх влево", "центрируй дискорд"
        VoicePattern(
            regex=r"^(?:помести|поставь|прицепи)\s+(.+)\s+(?:в\s+)?(верхний\s+левый|верхний\s+правый|нижний\s+левый|нижний\s+правый)(?:\s+угол)?$",
            priority=190,
            args=lambda m: {
                "action": {
                    "верхний левый": "snap_top_left",
                    "верхний правый": "snap_top_right",
                    "нижний левый": "snap_bottom_left",
                    "нижний правый": "snap_bottom_right",
                }[m.group(2)],
                "window": m.group(1),
            },
        ),
        VoicePattern(
            regex=r"^(?:помести|поставь)\s+(.+)\s+(?:слева|влево)$",
            priority=180,
            args=lambda m: {"action": "snap_left", "window": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:помести|поставь)\s+(.+)\s+(?:справа|вправо)$",
            priority=180,
            args=lambda m: {"action": "snap_right", "window": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:центрируй|по\s*центру)\s+(.+)$",
            priority=180,
            args=lambda m: {"action": "snap_center", "window": m.group(1)},
        ),
        # UK corners + center: "постав хром зліва", "центруй діскорд"
        VoicePattern(
            regex=r"^(?:познач|постав|причепи)\s+(.+)\s+(?:у\s+)?(верхній\s+лівий|верхній\s+правий|нижній\s+лівий|нижній\s+правий)(?:\s+кут)?$",
            priority=190,
            args=lambda m: {
                "action": {
                    "верхній лівий": "snap_top_left",
                    "верхній правий": "snap_top_right",
                    "нижній лівий": "snap_bottom_left",
                    "нижній правий": "snap_bottom_right",
                }[m.group(2)],
                "window": m.group(1),
            },
        ),
        VoicePattern(
            regex=r"^(?:постав|пересунь)\s+(.+)\s+(?:зліва|вліво)$",
            priority=180,
            args=lambda m: {"action": "snap_left", "window": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:постав|пересунь)\s+(.+)\s+(?:справа|вправо)$",
            priority=180,
            args=lambda m: {"action": "snap_right", "window": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:центруй|по\s*центру)\s+(.+)$",
            priority=180,
            args=lambda m: {"action": "snap_center", "window": m.group(1)},
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
                "snap_top_left": "Snapped to top-left",
                "snap_top_right": "Snapped to top-right",
                "snap_bottom_left": "Snapped to bottom-left",
                "snap_bottom_right": "Snapped to bottom-right",
                "snap_center": "Centered",
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
    def _screen_size() -> tuple[int, int]:
        """Primary screen (width, height); 1920x1080 fallback off-Windows.

        The GetSystemMetrics call lives in platform/windows.py; off Windows
        it raises NotImplementedError and we fall back to a sane default.
        """
        try:
            return platform_ops.get_screen_metrics()
        except (NotImplementedError, OSError):
            return 1920, 1080

    # Snap action -> (x_frac, y_frac, w_frac, h_frac) of the screen.
    _SNAP_GEOMETRY: ClassVar[dict[str, tuple[float, float, float, float]]] = {
        "snap_left":         (0.0,  0.0,  0.5, 1.0),
        "snap_right":        (0.5,  0.0,  0.5, 1.0),
        "snap_top_left":     (0.0,  0.0,  0.5, 0.5),
        "snap_top_right":    (0.5,  0.0,  0.5, 0.5),
        "snap_bottom_left":  (0.0,  0.5,  0.5, 0.5),
        "snap_bottom_right": (0.5,  0.5,  0.5, 0.5),
        "snap_center":       (0.25, 0.25, 0.5, 0.5),
    }

    @classmethod
    def _perform_action(cls, win, action: str) -> None:
        """Execute the window action (runs in thread)."""
        if action == "focus":
            win.activate()
        elif action == "minimize":
            win.minimize()
        elif action == "maximize":
            win.maximize()
        elif action == "restore":
            win.restore()
        elif action in cls._SNAP_GEOMETRY:
            xf, yf, wf, hf = cls._SNAP_GEOMETRY[action]
            screen_w, screen_h = cls._screen_size()
            win.restore()
            win.moveTo(int(screen_w * xf), int(screen_h * yf))
            win.resizeTo(int(screen_w * wf), int(screen_h * hf))
        elif action == "close":
            win.close()


class SnapLayoutArgs(BaseModel):
    left: str = Field(description="App/window to snap to the left half.")
    right: str = Field(description="App/window to snap to the right half.")


class SnapLayoutTool:
    """Snap two windows side by side in one command (feature 3)."""

    name: str = "snap_layout"
    description: str = (
        "Arrange two windows side by side: one on the left half, one on the "
        "right. Example: 'VS Code left, Chrome right'. "
        "(RU: слева ... справа / UK: зліва ... справа)"
    )
    args_schema = SnapLayoutArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "vs code left, chrome right" / "snap vs code left and chrome right"
        VoicePattern(
            regex=r"^(?:snap\s+)?(.+?)\s+(?:on\s+the\s+)?left(?:\s+(?:half|side))?(?:\s*,|\s+and)\s+(.+?)\s+(?:on\s+the\s+)?right(?:\s+(?:half|side))?$",
            priority=150,
            args=lambda m: {"left": m.group(1), "right": m.group(2)},
        ),
        # RU: "vs code слева, chrome справа"
        VoicePattern(
            regex=r"^(.+?)\s+слева(?:\s*,|\s+и|\s+а)\s+(.+?)\s+справа$",
            priority=150,
            args=lambda m: {"left": m.group(1), "right": m.group(2)},
        ),
        # UK: "vs code зліва, chrome справа"
        VoicePattern(
            regex=r"^(.+?)\s+зліва(?:\s*,|\s+і|\s+а)\s+(.+?)\s+справа$",
            priority=150,
            args=lambda m: {"left": m.group(1), "right": m.group(2)},
        ),
    )

    async def execute(self, args: SnapLayoutArgs) -> ToolResult:
        gw = _get_pygetwindow()
        if gw is None:
            return ToolResult(
                success=False,
                error="pygetwindow not installed. Install: pip install pygetwindow",
            )
        action_tool = WindowActionTool()
        placed: list[str] = []
        for name, snap in ((args.left, "snap_left"), (args.right, "snap_right")):
            title = _resolve_app_to_title(name)
            win = _find_window(title) or _find_window(name)
            if win is None:
                return ToolResult(
                    success=False,
                    error=f"Window '{name}' not found, sir.",
                )
            try:
                await asyncio.to_thread(action_tool._perform_action, win, snap)
                placed.append(win.title)
            except Exception as e:
                log.exception("snap_layout failed for %r", name)
                return ToolResult(success=False, error=f"Layout failed: {e}")
        return ToolResult(
            success=True,
            output=f"Arranged {placed[0]} left and {placed[1]} right, sir.",
        )


class MinimizeAllArgs(BaseModel):
    pass


class MinimizeAllTool:
    """Minimize every window / show the desktop (feature 4)."""

    name: str = "minimize_all"
    description: str = (
        "Minimize all windows and show the desktop (Win+D). "
        "(RU: сверни всё, покажи рабочий стол / UK: згорни все, покажи робочий стіл)"
    )
    args_schema = MinimizeAllArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:minimize|minimise|hide)\s+all(?:\s+windows)?$", priority=160),
        VoicePattern(regex=r"^show\s+(?:the\s+)?desktop$", priority=160),
        VoicePattern(regex=r"^сверни\s+(?:вс[её]|все\s+окна)$", priority=160),
        VoicePattern(regex=r"^покажи\s+рабочий\s+стол$", priority=160),
        VoicePattern(regex=r"^згорни\s+(?:вс[еі]|всі\s+вікна)$", priority=160),
        VoicePattern(regex=r"^покажи\s+робочий\s+стіл$", priority=160),
    )

    async def execute(self, args: MinimizeAllArgs) -> ToolResult:
        ok = await asyncio.to_thread(_send_keys, "win", "d")
        if not ok:
            return ToolResult(
                success=False,
                error="Could not send the shortcut (pyautogui unavailable), sir.",
            )
        return ToolResult(success=True, output="Minimized all windows, sir.")


class RestoreAllTool:
    """Restore all windows / undo show-desktop (feature 4)."""

    name: str = "restore_all"
    description: str = (
        "Restore all minimized windows (Win+D toggle). "
        "(RU: разверни всё / UK: розгорни все)"
    )
    args_schema = MinimizeAllArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:restore|unhide|show)\s+all(?:\s+windows)?$", priority=160),
        VoicePattern(regex=r"^разверни\s+(?:вс[её]|все\s+окна)$", priority=160),
        VoicePattern(regex=r"^розгорни\s+(?:вс[еі]|всі\s+вікна)$", priority=160),
    )

    async def execute(self, args: MinimizeAllArgs) -> ToolResult:
        ok = await asyncio.to_thread(_send_keys, "win", "d")
        if not ok:
            return ToolResult(
                success=False,
                error="Could not send the shortcut (pyautogui unavailable), sir.",
            )
        return ToolResult(success=True, output="Restored all windows, sir.")


class VirtualDesktopArgs(BaseModel):
    action: Literal["next", "previous", "task_view"] = Field(
        description="Virtual-desktop action: next, previous, or task_view."
    )


class VirtualDesktopTool:
    """Switch virtual desktops / open Task View (feature 5)."""

    name: str = "virtual_desktop"
    description: str = (
        "Switch virtual desktops (next / previous) or open Task View. "
        "(RU: следующий/предыдущий рабочий стол / UK: наступний/попередній стіл)"
    )
    args_schema = VirtualDesktopArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(regex=r"^(?:next|switch\s+to\s+next)\s+desktop$", priority=160,
                     args=lambda m: {"action": "next"}),
        VoicePattern(regex=r"^(?:previous|prev|last)\s+desktop$", priority=160,
                     args=lambda m: {"action": "previous"}),
        VoicePattern(regex=r"^(?:task\s*view|show\s+desktops)$", priority=160,
                     args=lambda m: {"action": "task_view"}),
        # RU
        VoicePattern(regex=r"^следующий\s+(?:рабочий\s+)?стол$", priority=160,
                     args=lambda m: {"action": "next"}),
        VoicePattern(regex=r"^предыдущий\s+(?:рабочий\s+)?стол$", priority=160,
                     args=lambda m: {"action": "previous"}),
        # UK
        VoicePattern(regex=r"^наступний\s+(?:робочий\s+)?стіл$", priority=160,
                     args=lambda m: {"action": "next"}),
        VoicePattern(regex=r"^попередній\s+(?:робочий\s+)?стіл$", priority=160,
                     args=lambda m: {"action": "previous"}),
    )

    async def execute(self, args: VirtualDesktopArgs) -> ToolResult:
        if args.action == "next":
            ok = await asyncio.to_thread(_send_keys, "win", "ctrl", "right")
            msg = "Switched to the next desktop, sir."
        elif args.action == "previous":
            ok = await asyncio.to_thread(_send_keys, "win", "ctrl", "left")
            msg = "Switched to the previous desktop, sir."
        else:  # task_view
            ok = await asyncio.to_thread(_send_keys, "win", "tab")
            msg = "Opened Task View, sir."
        if not ok:
            return ToolResult(
                success=False,
                error="Could not send the shortcut (pyautogui unavailable), sir.",
            )
        return ToolResult(success=True, output=msg)


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
