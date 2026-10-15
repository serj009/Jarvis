"""Startup manager — list, add, remove programs from Windows startup.

Uses winreg to manage HKCU/HKLM Run keys and the shell:startup folder.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_winreg():
    """Lazy import winreg (stdlib on Windows)."""
    try:
        import winreg
        return winreg
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HKCU_RUN = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_HKLM_RUN = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _list_startup_impl() -> list[dict]:
    """List all startup programs from registry + shell:startup folder."""
    winreg = _get_winreg()
    if winreg is None:
        raise NotImplementedError("winreg is not available (Windows-only).")

    entries: list[dict] = []

    # HKCU\...\Run
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HKCU_RUN, 0, winreg.KEY_READ)
        try:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    entries.append({
                        "name": name,
                        "command": value,
                        "source": "HKCU\\Run",
                        "removable": True,
                    })
                    i += 1
                except OSError:
                    break
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        pass

    # HKLM\...\Run (read-only, may need elevation to modify)
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _HKLM_RUN, 0, winreg.KEY_READ)
        try:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    entries.append({
                        "name": name,
                        "command": value,
                        "source": "HKLM\\Run",
                        "removable": False,  # needs elevation
                    })
                    i += 1
                except OSError:
                    break
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        pass

    # shell:startup folder
    startup_folder = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )
    if os.path.isdir(startup_folder):
        for fname in os.listdir(startup_folder):
            fpath = os.path.join(startup_folder, fname)
            if os.path.isfile(fpath):
                entries.append({
                    "name": fname,
                    "command": fpath,
                    "source": "Startup folder",
                    "removable": True,
                })

    return entries


def _remove_from_startup_impl(name: str) -> str:
    """Remove a program from HKCU startup registry."""
    winreg = _get_winreg()
    if winreg is None:
        raise NotImplementedError("winreg is not available (Windows-only).")

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _HKCU_RUN, 0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, name)
        finally:
            winreg.CloseKey(key)
        return f"Removed '{name}' from startup, sir."
    except FileNotFoundError:
        # Try startup folder
        startup_folder = os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        )
        fpath = os.path.join(startup_folder, name)
        if os.path.isfile(fpath):
            os.remove(fpath)
            return f"Removed '{name}' from startup folder, sir."
        return f"'{name}' not found in startup entries, sir."


def _add_to_startup_impl(name: str, command: str) -> str:
    """Add a program to HKCU startup registry."""
    winreg = _get_winreg()
    if winreg is None:
        raise NotImplementedError("winreg is not available (Windows-only).")

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _HKCU_RUN, 0, winreg.KEY_SET_VALUE,
    )
    try:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)
    return f"Added '{name}' to startup, sir."


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class RemoveFromStartupArgs(BaseModel):
    name: str = Field(description="Name of the startup entry to remove.")


class AddToStartupArgs(BaseModel):
    name: str = Field(description="Display name for the startup entry.")
    command: str = Field(
        description="Full path to the executable or command to run at startup.",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ListStartupTool:
    """List all programs in Windows startup."""

    name: str = "list_startup"
    description: str = (
        "List all programs configured to start automatically with Windows. "
        "Shows registry entries (HKCU/HKLM Run) and startup folder items. "
        "(RU: что в автозагрузке / UK: що в автозавантаженні)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:what\s+)?(?:starts?\s+(?:on|at)\s+boot|startup\s+(?:programs|list|items))$",
            priority=200,
        ),
        # RU
        VoicePattern(
            regex=r"^что\s+в\s+автозагрузке$",
            priority=200,
        ),
        # UK
        VoicePattern(
            regex=r"^що\s+в\s+автозавантаженні$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            entries = await asyncio.to_thread(_list_startup_impl)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("list_startup failed")
            return ToolResult(success=False, error=f"Failed to list startup: {e}")

        if not entries:
            return ToolResult(success=True, output="No startup programs found, sir.")

        lines = [f"Found {len(entries)} startup item(s), sir:"]
        for e in entries:
            removable = " [removable]" if e.get("removable") else ""
            lines.append(
                f"  • {e['name']} — {e['source']}{removable}\n"
                f"    {e['command']}"
            )
        return ToolResult(success=True, output="\n".join(lines))


class RemoveFromStartupTool:
    """Remove a program from Windows startup."""

    name: str = "remove_from_startup"
    description: str = (
        "Remove a program from Windows startup (HKCU registry or startup folder). "
        "(RU: убери из автозагрузки / UK: прибери з автозавантаження)"
    )
    args_schema = RemoveFromStartupArgs
    requires_confirmation: bool = False  # Gated via TOOL_RISK_MAP = DESTRUCTIVE
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^remove\s+(.+)\s+from\s+startup$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        # RU
        VoicePattern(
            regex=r"^убери\s+(.+)\s+из\s+автозагрузки$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        # UK
        VoicePattern(
            regex=r"^прибери\s+(.+)\s+з\s+автозавантаження$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
    )

    async def execute(self, args: RemoveFromStartupArgs) -> ToolResult:
        try:
            msg = await asyncio.to_thread(_remove_from_startup_impl, args.name)
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("remove_from_startup failed")
            return ToolResult(success=False, error=f"Failed to remove from startup: {e}")


class AddToStartupTool:
    """Add a program to Windows startup."""

    name: str = "add_to_startup"
    description: str = (
        "Add a program to Windows startup (HKCU registry). "
        "(RU: добавь в автозагрузку / UK: додай в автозавантаження)"
    )
    args_schema = AddToStartupArgs
    requires_confirmation: bool = False  # Gated via TOOL_RISK_MAP = WRITE
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^add\s+(.+)\s+to\s+startup$",
            priority=200,
            args=lambda m: {"name": m.group(1), "command": m.group(1)},
        ),
        # RU
        VoicePattern(
            regex=r"^добавь\s+(.+)\s+в\s+автозагрузку$",
            priority=200,
            args=lambda m: {"name": m.group(1), "command": m.group(1)},
        ),
        # UK
        VoicePattern(
            regex=r"^додай\s+(.+)\s+в\s+автозавантаження$",
            priority=200,
            args=lambda m: {"name": m.group(1), "command": m.group(1)},
        ),
    )

    async def execute(self, args: AddToStartupArgs) -> ToolResult:
        try:
            msg = await asyncio.to_thread(
                _add_to_startup_impl, args.name, args.command,
            )
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("add_to_startup failed")
            return ToolResult(success=False, error=f"Failed to add to startup: {e}")
