"""Package manager — install/uninstall programs via winget.

winget is built into Windows 10/11. Install/uninstall are destructive
operations requiring user confirmation via the risk system.
Search and list are read-only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


def _run_winget(args: list[str], timeout: int = 120) -> dict:
    """Run a winget command and return parsed output."""
    import subprocess

    cmd = ["winget"] + args + ["--accept-source-agreements"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "return_code": result.returncode,
        }
    except FileNotFoundError:
        return {"success": False, "error": "winget not found. Windows 10/11 required."}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"winget timed out after {timeout}s."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _check_winget() -> bool:
    """Check if winget is available."""
    import subprocess
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


class PackageSearchArgs(BaseModel):
    query: str = Field(description="Package name or search query.")


class PackageInstallArgs(BaseModel):
    package: str = Field(
        description="Package name or winget ID to install.",
    )


class PackageUninstallArgs(BaseModel):
    package: str = Field(
        description="Package name or winget ID to uninstall.",
    )


class PackageSearchTool:
    """Search for packages via winget (read-only)."""

    name: str = "package_search"
    description: str = (
        "Search for available packages to install via winget. "
        "Returns matching package names and IDs. "
        "(RU: найди программу, поищи программу / UK: знайди програму, пошукай програму)"
    )
    args_schema = PackageSearchArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:search|find)\s+(?:package|program|app)\s+(.+)$",
            priority=250,
            args=lambda m: {"query": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:найди|поищи)\s+(?:программу|приложение)\s+(.+)$",
            priority=250,
            args=lambda m: {"query": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:знайди|пошукай)\s+(?:програму|додаток)\s+(.+)$",
            priority=250,
            args=lambda m: {"query": m.group(1)},
        ),
    )

    async def execute(self, args: PackageSearchArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(
                _run_winget, ["search", args.query], 30,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {e}")

        if not result.get("success"):
            return ToolResult(
                success=False,
                error=result.get("error") or result.get("stderr") or "Search failed.",
            )

        output = result.get("output", "")
        if not output or "No package found" in output:
            return ToolResult(
                success=True,
                output=f"No packages found for '{args.query}', sir.",
            )

        # Truncate long output.
        lines = output.split("\n")
        if len(lines) > 25:
            output = "\n".join(lines[:25]) + f"\n... ({len(lines)} total results)"

        return ToolResult(
            success=True,
            output=f"Search results for '{args.query}':\n{output}",
        )


class PackageInstallTool:
    """Install a package via winget (destructive — requires confirmation)."""

    name: str = "package_install"
    description: str = (
        "Install a program/package via winget. Requires confirmation. "
        "(RU: установи программу / UK: встанови програму)"
    )
    args_schema = PackageInstallArgs
    requires_confirmation: bool = False  # Gated via TOOL_RISK_MAP = DESTRUCTIVE
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^install\s+(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
        VoicePattern(
            regex=r"^установи\s+(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
        VoicePattern(
            regex=r"^встанови\s+(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
    )

    async def execute(self, args: PackageInstallArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(
                _run_winget, ["install", args.package, "--silent"], 180,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Install failed: {e}")

        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Successfully installed {args.package}, sir.",
            )
        error = result.get("error") or result.get("stderr") or "Install failed."
        return ToolResult(success=False, error=error)


class PackageUninstallTool:
    """Uninstall a package via winget (destructive — requires confirmation)."""

    name: str = "package_uninstall"
    description: str = (
        "Uninstall a program/package via winget. Requires confirmation. "
        "(RU: удали программу / UK: видали програму)"
    )
    args_schema = PackageUninstallArgs
    requires_confirmation: bool = False  # Gated via TOOL_RISK_MAP = DESTRUCTIVE
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^uninstall\s+(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
        VoicePattern(
            regex=r"^удали\s+(?:программу\s+)?(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
        VoicePattern(
            regex=r"^видали\s+(?:програму\s+)?(.+)$",
            priority=250,
            args=lambda m: {"package": m.group(1)},
        ),
    )

    async def execute(self, args: PackageUninstallArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(
                _run_winget, ["uninstall", args.package, "--silent"], 120,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Uninstall failed: {e}")

        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Successfully uninstalled {args.package}, sir.",
            )
        error = result.get("error") or result.get("stderr") or "Uninstall failed."
        return ToolResult(success=False, error=error)
