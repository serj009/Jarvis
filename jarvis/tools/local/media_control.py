"""Browser media control — play, pause, next/prev track, fullscreen.

Two control paths:

1. **Selenium JS injection** (if the BrowserController has an active
   driver): directly controls HTML5 ``<video>``/``<audio>`` elements
   on YouTube, Spotify Web, etc.

2. **System media keys** (fallback): simulates media-key presses via
   ``pynput`` or ``pyautogui``.  Works with any media player that
   honours system-wide media keys.

All external dependencies are lazy-imported with try/except.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System media keys (fallback)
# ---------------------------------------------------------------------------

def _press_media_key(key_name: str) -> dict[str, Any]:
    """Simulate a media key press.  Tries pynput, then pyautogui."""
    # pynput path
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        key_map = {
            "play_pause": Key.media_play_pause,
            "next": Key.media_next,
            "prev": Key.media_previous,
            "stop": Key.media_play_pause,  # toggle
            "volume_up": Key.media_volume_up,
            "volume_down": Key.media_volume_down,
            "mute": Key.media_volume_mute,
        }
        key = key_map.get(key_name)
        if key:
            kb.press(key)
            kb.release(key)
            return {"success": True, "method": "pynput", "key": key_name}
    except ImportError:
        pass
    except Exception as exc:
        log.debug("pynput media key failed: %s", exc)

    # pyautogui path
    try:
        import pyautogui
        key_map_auto = {
            "play_pause": "playpause",
            "next": "nexttrack",
            "prev": "prevtrack",
            "stop": "playpause",
            "volume_up": "volumeup",
            "volume_down": "volumedown",
            "mute": "volumemute",
        }
        auto_key = key_map_auto.get(key_name)
        if auto_key:
            pyautogui.press(auto_key)
            return {"success": True, "method": "pyautogui", "key": key_name}
    except ImportError:
        pass
    except Exception as exc:
        log.debug("pyautogui media key failed: %s", exc)

    return {"success": False, "error": "Neither pynput nor pyautogui available for media keys."}


# ---------------------------------------------------------------------------
# Selenium JS media control
# ---------------------------------------------------------------------------

_JS_MEDIA_ACTIONS: dict[str, str] = {
    "pause": "var m=document.querySelector('video,audio'); if(m) m.pause();",
    "play": "var m=document.querySelector('video,audio'); if(m) m.play();",
    "toggle": (
        "var m=document.querySelector('video,audio');"
        "if(m){m.paused?m.play():m.pause();return !m.paused;}"
    ),
    "fullscreen": "var v=document.querySelector('video'); if(v) v.requestFullscreen();",
    "exit_fullscreen": "document.exitFullscreen();",
    "skip_forward": (
        "var m=document.querySelector('video,audio');"
        "if(m) m.currentTime+=10;"
    ),
    "skip_backward": (
        "var m=document.querySelector('video,audio');"
        "if(m) m.currentTime-=10;"
    ),
}


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class MediaControlArgs(BaseModel):
    action: Literal[
        "pause", "play", "toggle",
        "next_track", "prev_track",
        "fullscreen", "exit_fullscreen",
        "skip_forward", "skip_backward",
    ] = Field(description=(
        "Media action: pause, play, toggle (play/pause), next_track, "
        "prev_track, fullscreen, exit_fullscreen, skip_forward (+10s), "
        "skip_backward (-10s)."
    ))


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class MediaControlTool:
    name: str = "media_control"
    description: str = (
        "Controls media playback in the browser or system-wide. "
        "Use when the user says pause, play, resume, next track, previous "
        "track, fullscreen, or skip forward/backward in video/audio. "
        "First tries Selenium JS injection (YouTube, Spotify Web); "
        "falls back to system media keys (pynput/pyautogui). "
        "(RU: пауза, продолжи, стоп, следующий трек, предыдущий трек, "
        "полный экран / UK: пауза, продовжи, стоп, наступний трек, "
        "попередній трек, повний екран)"
    )
    args_schema = MediaControlArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^pause$",
            priority=15,
            args=lambda m: {"action": "pause"},
        ),
        VoicePattern(
            regex=r"^(?:play|resume|continue)$",
            priority=15,
            args=lambda m: {"action": "play"},
        ),
        VoicePattern(
            regex=r"^(?:next|skip)\s+(?:track|song)$",
            priority=15,
            args=lambda m: {"action": "next_track"},
        ),
        VoicePattern(
            regex=r"^(?:previous|prev|last)\s+(?:track|song)$",
            priority=15,
            args=lambda m: {"action": "prev_track"},
        ),
        VoicePattern(
            regex=r"^(?:full\s*screen|fullscreen)$",
            priority=15,
            args=lambda m: {"action": "fullscreen"},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:пауза|стоп|останов[иь])$",
            priority=15,
            args=lambda m: {"action": "pause"},
        ),
        VoicePattern(
            regex=r"^(?:продолжи|воспроизвед[иь]|играй|дальше)$",
            priority=15,
            args=lambda m: {"action": "play"},
        ),
        VoicePattern(
            regex=r"^следующ(?:ий|ая|ее)\s+(?:трек|песн[яю]|видео)$",
            priority=15,
            args=lambda m: {"action": "next_track"},
        ),
        VoicePattern(
            regex=r"^полный\s+экран$",
            priority=15,
            args=lambda m: {"action": "fullscreen"},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:пауза|стоп|зупини)$",
            priority=15,
            args=lambda m: {"action": "pause"},
        ),
        VoicePattern(
            regex=r"^(?:продовжи|грай|далі)$",
            priority=15,
            args=lambda m: {"action": "play"},
        ),
        VoicePattern(
            regex=r"^наступн(?:ий|а|е)\s+(?:трек|пісн[яю]|відео)$",
            priority=15,
            args=lambda m: {"action": "next_track"},
        ),
    )

    async def execute(self, args: MediaControlArgs) -> ToolResult:
        action = args.action

        # Try Selenium JS first
        try:
            from jarvis.tools.local.browser_tabs import _get_browser
            bc = _get_browser()
            if bc.driver_active:
                js_action = action
                if action in ("next_track", "prev_track"):
                    js_action = None  # No JS equivalent — use media keys
                elif action in _JS_MEDIA_ACTIONS:
                    js_action = action

                if js_action and js_action in _JS_MEDIA_ACTIONS:
                    result = await asyncio.to_thread(
                        bc.execute_js, _JS_MEDIA_ACTIONS[js_action],
                    )
                    if result.get("success"):
                        return ToolResult(
                            success=True,
                            output=self._spoken(action),
                        )
        except Exception:
            log.debug("Selenium media control failed", exc_info=True)

        # Fallback to system media keys
        media_key = {
            "pause": "play_pause",
            "play": "play_pause",
            "toggle": "play_pause",
            "next_track": "next",
            "prev_track": "prev",
        }.get(action)

        if media_key:
            result = await asyncio.to_thread(_press_media_key, media_key)
            if result.get("success"):
                return ToolResult(success=True, output=self._spoken(action))
            return ToolResult(success=False, error=result.get("error", "Media key failed"))

        # Actions only available via Selenium
        if action in ("fullscreen", "exit_fullscreen", "skip_forward", "skip_backward"):
            return ToolResult(
                success=False,
                error=f"{action} requires an active Selenium browser session.",
            )

        return ToolResult(success=False, error=f"Unknown action {action!r}")

    @staticmethod
    def _spoken(action: str) -> str:
        labels = {
            "pause": "Paused, sir.",
            "play": "Playing, sir.",
            "toggle": "Toggled playback, sir.",
            "next_track": "Next track, sir.",
            "prev_track": "Previous track, sir.",
            "fullscreen": "Fullscreen mode, sir.",
            "exit_fullscreen": "Exited fullscreen, sir.",
            "skip_forward": "Skipped forward, sir.",
            "skip_backward": "Skipped backward, sir.",
        }
        return labels.get(action, f"{action} done, sir.")
