"""Display management — brightness, night mode, resolution, monitor info.

Uses WMI / ctypes for brightness control, Windows registry for Night Light,
and ctypes EnumDisplaySettings for resolution information.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_ctypes():
    """Lazy import ctypes."""
    try:
        import ctypes
        return ctypes
    except ImportError:
        return None


def _get_wmi():
    """Lazy import wmi."""
    try:
        import wmi
        return wmi
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _set_brightness_impl(level: int) -> str:
    """Set monitor brightness to *level* (0-100)."""
    wmi_mod = _get_wmi()
    if wmi_mod is not None:
        try:
            c = wmi_mod.WMI(namespace="wmi")
            methods = c.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(level, 0)
            return f"Brightness set to {level}%, sir."
        except Exception as exc:
            log.debug("WMI brightness failed: %s", exc)

    # Fallback: ctypes Dxva2 SetMonitorBrightness (physical monitors)
    ctypes = _get_ctypes()
    if ctypes is not None:
        try:
            from ctypes import windll, byref, c_ulong, POINTER, Structure

            # Try to get physical monitor handle
            user32 = windll.user32
            hmon = user32.MonitorFromWindow(0, 1)  # MONITOR_DEFAULTTOPRIMARY

            class PHYSICAL_MONITOR(Structure):
                _fields_ = [
                    ("hPhysicalMonitor", c_ulong),
                    ("szPhysicalMonitorDescription", ctypes.c_wchar * 128),
                ]

            dxva2 = windll.Dxva2
            count = c_ulong()
            dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, byref(count))
            if count.value > 0:
                monitors = (PHYSICAL_MONITOR * count.value)()
                dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count, monitors)
                dxva2.SetMonitorBrightness(monitors[0].hPhysicalMonitor, level)
                dxva2.DestroyPhysicalMonitors(count, monitors)
                return f"Brightness set to {level}%, sir."
        except Exception as exc:
            log.debug("ctypes brightness failed: %s", exc)

    raise NotImplementedError(
        "Brightness control requires WMI or a DDC/CI capable monitor. "
        "Install: pip install wmi"
    )


def _get_brightness_impl() -> int | None:
    """Get current monitor brightness (0-100) or None."""
    wmi_mod = _get_wmi()
    if wmi_mod is not None:
        try:
            c = wmi_mod.WMI(namespace="wmi")
            brightness = c.WmiMonitorBrightness()[0]
            return int(brightness.CurrentBrightness)
        except Exception:
            pass
    return None


def _toggle_night_mode_impl(enable: bool) -> str:
    """Toggle Windows Night Light via registry."""
    try:
        import winreg
        key_path = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store"
            r"\DefaultAccount\Current\default$windows.data.bluelightreduction.settings"
            r"\windows.data.bluelightreduction.settings"
        )
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
        try:
            data, reg_type = winreg.QueryValueEx(key, "Data")
            data = bytearray(data)
            # Byte at offset 18 controls the toggle (0x15=enabled, 0x13=disabled)
            if len(data) > 18:
                if enable:
                    data[18] = 0x15
                else:
                    data[18] = 0x13
                winreg.SetValueEx(key, "Data", 0, reg_type, bytes(data))
        finally:
            winreg.CloseKey(key)

        state = "enabled" if enable else "disabled"
        return f"Night mode {state}, sir."
    except FileNotFoundError:
        raise NotImplementedError(
            "Night Light registry key not found. "
            "This may require Windows 10 1703+ or later."
        )
    except Exception as exc:
        raise NotImplementedError(f"Night mode toggle failed: {exc}")


def _display_info_impl() -> dict:
    """Gather monitor information."""
    ctypes = _get_ctypes()
    monitors: list[dict] = []

    if ctypes is not None:
        try:
            user32 = ctypes.windll.user32

            class DEVMODE(ctypes.Structure):
                _fields_ = [
                    ("dmDeviceName", ctypes.c_wchar * 32),
                    ("dmSpecVersion", ctypes.c_ushort),
                    ("dmDriverVersion", ctypes.c_ushort),
                    ("dmSize", ctypes.c_ushort),
                    ("dmDriverExtra", ctypes.c_ushort),
                    ("dmFields", ctypes.c_ulong),
                    ("dmPositionX", ctypes.c_long),
                    ("dmPositionY", ctypes.c_long),
                    ("dmDisplayOrientation", ctypes.c_ulong),
                    ("dmDisplayFixedOutput", ctypes.c_ulong),
                    ("dmColor", ctypes.c_short),
                    ("dmDuplex", ctypes.c_short),
                    ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short),
                    ("dmCollate", ctypes.c_short),
                    ("dmFormName", ctypes.c_wchar * 32),
                    ("dmLogPixels", ctypes.c_ushort),
                    ("dmBitsPerPel", ctypes.c_ulong),
                    ("dmPelsWidth", ctypes.c_ulong),
                    ("dmPelsHeight", ctypes.c_ulong),
                    ("dmDisplayFlags", ctypes.c_ulong),
                    ("dmDisplayFrequency", ctypes.c_ulong),
                ]

            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)

            # Enumerate display devices
            i = 0
            while True:
                try:
                    class DISPLAY_DEVICE(ctypes.Structure):
                        _fields_ = [
                            ("cb", ctypes.c_ulong),
                            ("DeviceName", ctypes.c_wchar * 32),
                            ("DeviceString", ctypes.c_wchar * 128),
                            ("StateFlags", ctypes.c_ulong),
                            ("DeviceID", ctypes.c_wchar * 128),
                            ("DeviceKey", ctypes.c_wchar * 128),
                        ]

                    dd = DISPLAY_DEVICE()
                    dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
                    if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                        break

                    # Only active monitors
                    DISPLAY_DEVICE_ACTIVE = 0x00000001
                    if dd.StateFlags & DISPLAY_DEVICE_ACTIVE:
                        dm2 = DEVMODE()
                        dm2.dmSize = ctypes.sizeof(DEVMODE)
                        ENUM_CURRENT_SETTINGS = -1
                        if user32.EnumDisplaySettingsW(
                            dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(dm2)
                        ):
                            monitors.append({
                                "name": dd.DeviceName.strip('\x00'),
                                "adapter": dd.DeviceString.strip('\x00'),
                                "width": dm2.dmPelsWidth,
                                "height": dm2.dmPelsHeight,
                                "refresh_hz": dm2.dmDisplayFrequency,
                                "bits_per_pixel": dm2.dmBitsPerPel,
                            })
                    i += 1
                except Exception:
                    break

            # Fallback: at least the primary
            if not monitors:
                w = user32.GetSystemMetrics(0)
                h = user32.GetSystemMetrics(1)
                monitors.append({
                    "name": "Primary",
                    "width": w,
                    "height": h,
                })
        except Exception as exc:
            log.debug("ctypes display info failed: %s", exc)

    brightness = _get_brightness_impl()
    return {
        "monitor_count": len(monitors),
        "monitors": monitors,
        "brightness": brightness,
    }


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
