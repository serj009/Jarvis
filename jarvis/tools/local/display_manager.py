"""Display management — brightness, night mode, resolution, monitor info.

The WMI / ctypes / winreg access that drives brightness, Night Light and
monitor enumeration lives in platform/windows.py (ARCHITECTURE RULE,
SPEC.md line 75: no win32 imports outside platform/windows.py). This module
only wires the Tool classes to the platform layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis import platform as platform_ops
from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend helpers — thin delegators to the platform layer.
#
# These keep the _*_impl names the tools and tests reference while routing the
# work through the per-OS backend (Windows does it for real via WMI / ctypes /
# winreg; macOS/Linux raise NotSupportedError, which the tools report as a
# ToolResult error).
# ---------------------------------------------------------------------------

def _set_brightness_impl(level: int) -> str:
    """Set monitor brightness to *level* (0-100)."""
    return platform_ops.set_brightness(level)


def _get_brightness_impl() -> int | None:
    """Get current monitor brightness (0-100) or None."""
    return platform_ops.get_brightness()


def _toggle_night_mode_impl(enable: bool) -> str:
    """Toggle Windows Night Light via registry."""
    return platform_ops.toggle_night_mode(enable)


def _display_info_impl() -> dict:
    """Gather monitor information."""
    return platform_ops.display_info()


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class SetBrightnessArgs(BaseModel):
    level: int = Field(
        ge=0, le=100,
        description="Brightness level 0-100.",
    )


class ToggleNightModeArgs(BaseModel):
    enable: bool = Field(
        description="True to enable night mode, False to disable.",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class SetBrightnessTool:
    """Set display brightness level."""

    name: str = "set_brightness"
    description: str = (
        "Set the monitor brightness to a percentage (0-100). "
        "(RU: яркость экрана / UK: яскравість екрану)"
    )
    args_schema = SetBrightnessArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "brightness 50%", "brightness 50", "set brightness to 80"
        VoicePattern(
            regex=r"^(?:set\s+)?brightness(?:\s+(?:to\s+)?)?(\d{1,3})\s*%?$",
            priority=200,
            args=lambda m: {"level": int(m.group(1))},
        ),
        # RU: "яркость 50", "яркость на 80"
        VoicePattern(
            regex=r"^яркость(?:\s+(?:на\s+)?)?(\d{1,3})\s*%?$",
            priority=200,
            args=lambda m: {"level": int(m.group(1))},
        ),
        # UK: "яскравість 50", "яскравість на 80"
        VoicePattern(
            regex=r"^яскравість(?:\s+(?:на\s+)?)?(\d{1,3})\s*%?$",
            priority=200,
            args=lambda m: {"level": int(m.group(1))},
        ),
    )

    async def execute(self, args: SetBrightnessArgs) -> ToolResult:
        try:
            msg = await asyncio.to_thread(_set_brightness_impl, args.level)
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("set_brightness failed")
            return ToolResult(success=False, error=f"Brightness control failed: {e}")


class ToggleNightModeTool:
    """Toggle Windows Night Light mode."""

    name: str = "toggle_night_mode"
    description: str = (
        "Enable or disable Windows Night Light (blue light filter). "
        "(RU: ночной режим / UK: нічний режим)"
    )
    args_schema = ToggleNightModeArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "night mode on", "night mode off"
        VoicePattern(
            regex=r"^night\s+(?:mode|light)\s+(on|off)$",
            priority=200,
            args=lambda m: {"enable": m.group(1) == "on"},
        ),
        # RU: "ночной режим", "ночной режим включи/выключи"
        VoicePattern(
            regex=r"^ночной\s+режим(?:\s+(включи|выключи))?$",
            priority=200,
            args=lambda m: {"enable": m.group(1) != "выключи" if m.group(1) else True},
        ),
        # UK: "нічний режим", "нічний режим увімкни/вимкни"
        VoicePattern(
            regex=r"^нічний\s+режим(?:\s+(увімкни|вимкни))?$",
            priority=200,
            args=lambda m: {"enable": m.group(1) != "вимкни" if m.group(1) else True},
        ),
    )

    async def execute(self, args: ToggleNightModeArgs) -> ToolResult:
        try:
            msg = await asyncio.to_thread(_toggle_night_mode_impl, args.enable)
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("toggle_night_mode failed")
            return ToolResult(success=False, error=f"Night mode toggle failed: {e}")


class DisplayInfoTool:
    """Show information about connected monitors."""

    name: str = "display_info"
    description: str = (
        "Show information about connected monitors: count, resolution, "
        "refresh rate, brightness. "
        "(RU: информация о мониторах / UK: інформація про монітори)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "display info", "screen resolution", "monitor info"
        VoicePattern(
            regex=r"^(?:display|monitor|screen)\s+(?:info|information|resolution)$",
            priority=200,
        ),
        # RU: "информация о мониторах", "разрешение экрана"
        VoicePattern(
            regex=r"^(?:информация\s+о\s+монитор|разрешение\s+экрана).*$",
            priority=200,
        ),
        # UK: "інформація про монітори", "роздільна здатність"
        VoicePattern(
            regex=r"^(?:інформація\s+про\s+монітор|роздільна\s+здатність).*$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            info = await asyncio.to_thread(_display_info_impl)
        except Exception as e:
            log.exception("display_info failed")
            return ToolResult(success=False, error=f"Failed to get display info: {e}")

        monitors = info.get("monitors", [])
        if not monitors:
            return ToolResult(
                success=True,
                output="No display information available, sir.",
            )

        lines = [f"Found {info['monitor_count']} monitor(s), sir:"]
        for m in monitors:
            res = f"{m.get('width', '?')}x{m.get('height', '?')}"
            hz = m.get("refresh_hz")
            name = m.get("name", "Unknown")
            adapter = m.get("adapter", "")
            line = f"  • {name}: {res}"
            if hz:
                line += f" @ {hz}Hz"
            if adapter:
                line += f" ({adapter})"
            lines.append(line)

        brightness = info.get("brightness")
        if brightness is not None:
            lines.append(f"  Brightness: {brightness}%")

        return ToolResult(success=True, output="\n".join(lines))
