"""Hotkey sender — send keyboard shortcuts and key combinations.

Uses pyautogui for cross-platform key simulation.
Maintains a whitelist of safe combinations and requires confirmation
for dangerous ones (Alt+F4, Ctrl+W).
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_pyautogui():
    """Lazy import pyautogui."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        return pyautogui
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Safety whitelist
# ---------------------------------------------------------------------------

# Combinations that are safe to send without extra confirmation
_SAFE_HOTKEYS: frozenset[str] = frozenset({
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y",
    "ctrl+a", "ctrl+s", "ctrl+p", "ctrl+f", "ctrl+n",
    "ctrl+t", "ctrl+l", "ctrl+d", "ctrl+r",
    "ctrl+shift+t", "ctrl+shift+n", "ctrl+shift+esc",
    "alt+tab", "alt+shift+tab",
    "win+d", "win+e", "win+l", "win+r", "win+tab",
    "win+shift+s",  # Snipping tool
    "ctrl+shift+v",  # Paste without formatting
    "f5", "f11", "f12",
    "escape", "esc",
    "enter", "tab", "space",
    "ctrl+home", "ctrl+end",
    "home", "end", "pageup", "pagedown",
    "print_screen", "printscreen",
})

# Dangerous combinations that need confirmation via risk system
_DANGEROUS_HOTKEYS: frozenset[str] = frozenset({
    "alt+f4",       # Close window/app
    "ctrl+w",       # Close tab/window
    "ctrl+q",       # Quit app
    "ctrl+shift+q", # Force quit
    "alt+f4",       # System close
})


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _parse_hotkey(combo_str: str) -> list[str]:
    """Parse a hotkey string like 'ctrl+c' into ['ctrl', 'c']."""
    parts = combo_str.lower().replace(" ", "").split("+")
    key_map = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "win": "win",
        "windows": "win",
        "super": "win",
        "cmd": "win",
        "esc": "escape",
        "del": "delete",
        "ins": "insert",
        "pgup": "pageup",
        "pgdn": "pagedown",
        "printscreen": "printscreen",
        "print_screen": "printscreen",
        "prtsc": "printscreen",
    }
    return [key_map.get(p, p) for p in parts]


def _send_hotkey_impl(keys: list[str]) -> str:
    """Send a hotkey combination via pyautogui."""
    pyautogui = _get_pyautogui()
    if pyautogui is None:
        raise NotImplementedError(
            "Hotkey sender requires pyautogui. Install: pip install pyautogui"
        )

    try:
        pyautogui.hotkey(*keys)
        return f"Pressed {'+'.join(keys)}, sir."
    except Exception as exc:
        raise RuntimeError(f"Failed to send hotkey: {exc}")


def _is_dangerous(combo_str: str) -> bool:
    """Check if a combination is dangerous."""
    normalized = combo_str.lower().replace(" ", "")
    return normalized in _DANGEROUS_HOTKEYS


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class SendHotkeyArgs(BaseModel):
    combo: str = Field(
        description=(
            "Key combination to press, e.g. 'ctrl+c', 'alt+tab', "
            "'win+d', 'ctrl+shift+t'."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SendHotkeyTool:
    """Send a keyboard shortcut/hotkey combination."""

    name: str = "send_hotkey"
    description: str = (
        "Press a keyboard shortcut (e.g. Ctrl+C, Alt+Tab, Win+D). "
        "Supports ctrl, alt, shift, win modifiers plus any key. "
        "Dangerous combos (Alt+F4, Ctrl+W) require confirmation. "
        "(RU: нажми комбинацию клавиш / UK: натисни комбінацію клавіш)"
    )
    args_schema = SendHotkeyArgs
    requires_confirmation: bool = False  # Dynamic risk via TOOL_RISK_MAP
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "press ctrl c", "press alt tab", "hit ctrl+c"
        VoicePattern(
            regex=r"^(?:press|hit|send)\s+(.+)$",
            priority=300,  # Lower priority to not steal "press" from other tools
            args=lambda m: {"combo": m.group(1).replace(" ", "+")},
        ),
        # EN: "copy all" → ctrl+a then ctrl+c (special semantic mapping)
        VoicePattern(
            regex=r"^copy\s+all$",
            priority=200,
            args=lambda m: {"combo": "ctrl+a"},
        ),
        # RU: "нажми ctrl c", "нажми альт таб"
        VoicePattern(
            regex=r"^нажми\s+(.+)$",
            priority=300,
            args=lambda m: {"combo": m.group(1).replace(" ", "+")},
        ),
        # RU: "скопируй всё"
        VoicePattern(
            regex=r"^скопируй\s+всё$",
            priority=200,
            args=lambda m: {"combo": "ctrl+a"},
        ),
        # UK: "натисни ctrl c"
        VoicePattern(
            regex=r"^натисни\s+(.+)$",
            priority=300,
            args=lambda m: {"combo": m.group(1).replace(" ", "+")},
        ),
        # UK: "скопіюй все"
        VoicePattern(
            regex=r"^скопіюй\s+все$",
            priority=200,
            args=lambda m: {"combo": "ctrl+a"},
        ),
    )

    async def execute(self, args: SendHotkeyArgs) -> ToolResult:
        combo = args.combo.strip()
        keys = _parse_hotkey(combo)

        if not keys:
            return ToolResult(success=False, error="No keys specified, sir.")

        # Check if dangerous
        normalized = "+".join(keys)
        if _is_dangerous(normalized):
            return ToolResult(
                success=False,
                error=(
                    f"The combination {normalized} is potentially dangerous "
                    f"(may close windows/apps). Please confirm via the risk system."
                ),
            )

        try:
            msg = await asyncio.to_thread(_send_hotkey_impl, keys)
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except RuntimeError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("send_hotkey failed for %r", combo)
            return ToolResult(success=False, error=f"Hotkey failed: {e}")

    def confirmation_summary(self, args: SendHotkeyArgs) -> str:
        """Custom summary for dangerous hotkeys."""
        return f"Send keyboard shortcut: {args.combo}"
