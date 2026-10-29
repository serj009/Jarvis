"""Startup manager — list, add, remove programs from Windows startup.

The actual winreg / Startup-folder access lives in platform/windows.py
(ARCHITECTURE RULE, SPEC.md line 75: no win32 imports outside
platform/windows.py). This module only wires the Tool classes to the
platform layer.
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
# UAC / elevation helpers
# ---------------------------------------------------------------------------

_UAC_MSG = (
    "I can't do that without administrator privileges, sir. "
    "Try running JARVIS as admin, or do it manually."
)


def _is_access_denied(exc: OSError) -> bool:
    """Return True if *exc* is a Windows ERROR_ACCESS_DENIED (winerror 5)."""
    return getattr(exc, "winerror", None) == 5



# ---------------------------------------------------------------------------
# Backend helpers — thin delegators to the platform layer.
#
# These keep the _*_impl names the tools and tests reference while routing the
# work through the per-OS backend (Windows does it for real via winreg;
# macOS/Linux raise NotSupportedError, which the tools report as a
# ToolResult error).
# ---------------------------------------------------------------------------

def _list_startup_impl() -> list[dict]:
    """List all startup programs from registry + shell:startup folder."""
    return platform_ops.list_startup()


def _remove_from_startup_impl(name: str) -> str:
    """Remove a program from HKCU startup registry (or Startup folder)."""
    return platform_ops.remove_from_startup(name)


def _add_to_startup_impl(name: str, command: str) -> str:
    """Add a program to HKCU startup registry."""
    return platform_ops.add_to_startup(name, command)


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
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("list_startup failed")
            return ToolResult(success=False, error=f"Failed to list startup: {e}")
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
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("remove_from_startup failed")
            return ToolResult(success=False, error=f"Failed to remove from startup: {e}")
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
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("add_to_startup failed")
            return ToolResult(success=False, error=f"Failed to add to startup: {e}")
        except Exception as e:
            log.exception("add_to_startup failed")
            return ToolResult(success=False, error=f"Failed to add to startup: {e}")
