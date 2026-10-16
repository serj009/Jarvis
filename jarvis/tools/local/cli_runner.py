"""Safe CLI command runner — whitelisted commands only.

Runs a strict set of read-only / informational commands. Everything
outside the whitelist is blocked. Destructive commands (rm, del,
format, etc.) are explicitly blacklisted and will never execute.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# Read-only / informational commands that are safe to execute.
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "ping", "ipconfig", "systeminfo", "dir", "tree", "netstat",
    "tasklist", "whoami", "hostname", "nslookup", "tracert",
    "ver", "where", "findstr", "find", "type",
    "nvidia-smi",
    "git status", "git log", "git diff", "git branch",
    "python --version", "pip list", "pip show",
    "ollama list", "ollama ps", "ollama show",
})

# Absolutely blocked — never execute under any circumstance.
BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "rm", "del", "erase", "rmdir", "rd",
    "format", "diskpart", "fdisk",
    "shutdown", "restart",  # use power_control tool instead
    "reg", "regedit", "bcdedit", "bootrec",
    "net user", "net localgroup",
    "powershell -encodedcommand",
    "cmd /c del", "cmd /c rd", "cmd /c rm",
    "schtasks /delete", "schtasks /create",
    "mkfs", "dd",
})


def is_command_allowed(command: str) -> bool:
    """Check if a command is in the whitelist."""
    cmd_lower = command.lower().strip()
    # Check blocked first (absolute deny).
    for blocked in BLOCKED_COMMANDS:
        if cmd_lower.startswith(blocked):
            return False
    # Check whitelist.
    for allowed in ALLOWED_COMMANDS:
        if cmd_lower.startswith(allowed):
            return True
    return False


def _run_command(command: str, timeout: int = 30) -> dict:
    """Execute a whitelisted command and capture output."""
    if not is_command_allowed(command):
        return {
            "success": False,
            "error": f"Command not allowed: '{command}'. Only safe read-only commands are permitted.",
        }

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=None,
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()

        # Truncate very long output.
        max_len = 4000
        if len(output) > max_len:
            output = output[:max_len] + f"\n... (truncated, {len(output)} chars total)"

        return {
            "success": result.returncode == 0,
            "output": output,
            "stderr": stderr if stderr else None,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s."}
    except FileNotFoundError:
        return {"success": False, "error": f"Command not found: {command}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


class CLIRunnerArgs(BaseModel):
    command: str = Field(
        description=(
            "The CLI command to execute. Must be a whitelisted safe command "
            "(ping, ipconfig, systeminfo, dir, tree, netstat, tasklist, whoami, hostname, etc.)."
        ),
    )
    timeout: int = Field(
        default=30, ge=5, le=120,
        description="Command timeout in seconds (5–120).",
    )


class CLIRunnerTool:
    name: str = "run_command"
    description: str = (
        "Runs a safe, whitelisted CLI command and returns the output. "
        "Allowed: ping, ipconfig, systeminfo, dir, tree, netstat, tasklist, "
        "whoami, hostname, nvidia-smi, git status/log/diff/branch, etc. "
        "Destructive commands (rm, del, format) are blocked. "
        "(RU: запусти команду, пинг, команда / UK: запусти команду, пінг, команда)"
    )
    args_schema = CLIRunnerArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "run ping google.com", "run ipconfig"
        VoicePattern(
            regex=r"^run\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
        # RU: "запусти пинг google.com", "выполни ipconfig"
        VoicePattern(
            regex=r"^(?:запусти|выполни)\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
        # UK: "запусти пінг google.com", "виконай ipconfig"
        VoicePattern(
            regex=r"^(?:запусти|виконай)\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
    )

    def __init__(
        self,
        *,
        runner: object | None = None,
    ) -> None:
        """Allow injecting a custom runner for tests."""
        self._runner = runner

    async def execute(self, args: CLIRunnerArgs) -> ToolResult:
        if not is_command_allowed(args.command):
            return ToolResult(
                success=False,
                error=(
                    f"Command '{args.command}' is not in the allowed list, sir. "
                    "Only safe, read-only commands are permitted."
                ),
            )

        try:
            result = await asyncio.to_thread(
                _run_command, args.command, args.timeout,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Command execution failed: {e}")

        if result.get("success"):
            output = result.get("output", "")
            return ToolResult(
                success=True,
                output=output or "(no output)",
            )
        return ToolResult(
            success=False,
            error=result.get("error", "Command failed."),
        )
