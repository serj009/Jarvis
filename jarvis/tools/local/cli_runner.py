"""Safe CLI command runner — whitelisted commands only.

Runs a strict set of read-only / informational commands. Everything
outside the whitelist is blocked. Destructive commands (rm, del,
format, etc.) are explicitly blacklisted and will never execute.

Security model:
- Shell metacharacters (& | ; < > etc.) are rejected before parsing.
- Commands are split via shlex and compared token-by-token (no prefix
  matching).
- subprocess.run is called with a list of arguments, NEVER shell=True.
- Built-in cmd.exe commands (dir, ver, type, tree) are dispatched as
  ["cmd", "/c", cmd, *args].
- Git commands are checked for file-write arguments (--output, -o).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import sys
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

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


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shell metacharacter detection
# ---------------------------------------------------------------------------

# Characters that enable command chaining / redirection / expansion in
# cmd.exe, PowerShell, and bash.  Reject the command outright if any of
# these appear — even inside quotes (shlex would strip the quotes, but
# we want to be maximally conservative).
_DANGEROUS_CHARS_RE = re.compile(r"[&|;<>`!%\n\r]|\$\(")


def _contains_shell_metachar(command: str) -> bool:
    """Return True if *command* contains any shell metacharacter."""
    return bool(_DANGEROUS_CHARS_RE.search(command))


# ---------------------------------------------------------------------------
# Whitelist / blocklist — token-based matching
# ---------------------------------------------------------------------------

# Single-token commands (safe read-only / informational).
_ALLOWED_SINGLE: frozenset[str] = frozenset({
    "ping", "ipconfig", "systeminfo", "dir", "tree", "netstat",
    "tasklist", "whoami", "hostname", "nslookup", "tracert",
    "ver", "where", "findstr", "find", "type",
    "nvidia-smi",
})

# Two-token commands (first + second token must match exactly).
_ALLOWED_DOUBLE: frozenset[tuple[str, str]] = frozenset({
    ("git", "status"), ("git", "log"), ("git", "diff"), ("git", "branch"),
    ("python", "--version"),
    ("pip", "list"), ("pip", "show"),
    ("ollama", "list"), ("ollama", "ps"), ("ollama", "show"),
})

# Absolutely blocked single tokens — never execute.
_BLOCKED_SINGLE: frozenset[str] = frozenset({
    "rm", "del", "erase", "rmdir", "rd",
    "format", "diskpart", "fdisk",
    "shutdown", "restart",
    "reg", "regedit", "bcdedit", "bootrec",
    "mkfs", "dd",
    "schtasks",
    "powershell",
})

# Blocked two-token pairs.
_BLOCKED_DOUBLE: frozenset[tuple[str, str]] = frozenset({
    ("net", "user"), ("net", "localgroup"),
    ("cmd", "/c"),
})

# Commands built into cmd.exe that need ["cmd", "/c", ...] dispatch.
_CMD_BUILTINS: frozenset[str] = frozenset({
    "dir", "ver", "type", "tree",
})

# Arguments that could write to disk — blocked for git and other commands.
_WRITE_ARGS: frozenset[str] = frozenset({
    "--output", "-o", "--output-file", "--outfile",
    "-w",  # curl write
})


def _has_write_arg(tokens: list[str]) -> bool:
    """Return True if any token is a file-write argument."""
    for tok in tokens[1:]:  # skip the command itself
        lower = tok.lower()
        if lower in _WRITE_ARGS:
            return True
        if lower.startswith("--output=") or lower.startswith("-o="):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Legacy compat — kept for any external callers.
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    set(_ALLOWED_SINGLE)
    | {f"{a} {b}" for a, b in _ALLOWED_DOUBLE}
)
BLOCKED_COMMANDS: frozenset[str] = frozenset(
    set(_BLOCKED_SINGLE)
    | {f"{a} {b}" for a, b in _BLOCKED_DOUBLE}
)


def is_command_allowed(command: str) -> bool:
    """Check if *command* is in the whitelist (token-based, no prefix match).

    Returns False if the command contains shell metacharacters, is
    explicitly blocked, or is not in the allowed set.
    """
    cmd = command.strip()
    if not cmd:
        return False

    # 1. Reject shell metacharacters outright.
    if _contains_shell_metachar(cmd):
        return False

    # 2. Tokenize.
    try:
        tokens = shlex.split(cmd, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False

    first = tokens[0].lower()

    # 3. Check blocked (exact token match).
    if first in _BLOCKED_SINGLE:
        return False
    if len(tokens) >= 2:
        pair = (first, tokens[1].lower())
        if pair in _BLOCKED_DOUBLE:
            return False

    # 4. Check allowed (exact token match).
    if first in _ALLOWED_SINGLE:
        # Check for write arguments on git commands.
        if first == "git" and _has_write_arg(tokens):
            return False
        return True
    if len(tokens) >= 2:
        pair = (first, tokens[1].lower())
        if pair in _ALLOWED_DOUBLE:
            if first == "git" and _has_write_arg(tokens):
                return False
            return True

    return False


def _run_command(command: str, timeout: int = 30) -> dict:
    """Execute a whitelisted command and capture output.

    NEVER uses shell=True.  Commands are split via shlex and passed as a
    list to subprocess.run.  Built-in cmd.exe commands are dispatched
    through ``["cmd", "/c", ...]``.
    """
    if not is_command_allowed(command):
        return {
            "success": False,
            "error": (
                f"Command not allowed: '{command}'. "
                "Only safe read-only commands are permitted."
            ),
        }

    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError as exc:
        return {"success": False, "error": f"Failed to parse command: {exc}"}

    first = tokens[0].lower()

    # Built-in cmd.exe commands need ["cmd", "/c", ...].
    if first in _CMD_BUILTINS:
        run_args = ["cmd", "/c"] + tokens
    else:
        run_args = tokens

    try:
        result = subprocess.run(
            run_args,
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
            output = (
                output[:max_len]
                + f"\n... (truncated, {len(output)} chars total)"
            )

        # Detect access-denied from stderr.
        if (
            result.returncode != 0
            and stderr
            and "access is denied" in stderr.lower()
        ):
            return {"success": False, "error": _UAC_MSG}

        return {
            "success": result.returncode == 0,
            "output": output,
            "stderr": stderr if stderr else None,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout}s.",
        }
    except FileNotFoundError:
        return {"success": False, "error": f"Command not found: {command}"}
    except PermissionError:
        return {"success": False, "error": _UAC_MSG}
    except OSError as e:
        if _is_access_denied(e):
            return {"success": False, "error": _UAC_MSG}
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class CLIRunnerArgs(BaseModel):
    command: str = Field(
        description=(
            "The CLI command to execute. Must be a whitelisted safe command "
            "(ping, ipconfig, systeminfo, dir, tree, netstat, tasklist, "
            "whoami, hostname, etc.)."
        ),
    )
    timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Command timeout in seconds (5–120).",
    )


class CLIRunnerTool:
    name: str = "run_command"
    description: str = (
        "Runs a safe, whitelisted CLI command and returns the output. "
        "Allowed: ping, ipconfig, systeminfo, dir, tree, netstat, tasklist, "
        "whoami, hostname, nvidia-smi, git status/log/diff/branch, etc. "
        "Destructive commands (rm, del, format) are blocked. "
        "(RU: запусти команду, пинг / UK: запусти команду, пінг)"
    )
    args_schema = CLIRunnerArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^run\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:запусти|выполни)\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:запусти|виконай)\s+(.+)$",
            priority=800,
            args=lambda m: {"command": m.group(1)},
        ),
    )

    def __init__(self, *, runner: object | None = None) -> None:
        self._runner = runner

    async def execute(self, args: CLIRunnerArgs) -> ToolResult:
        if not is_command_allowed(args.command):
            return ToolResult(
                success=False,
                error=(
                    f"Command '{args.command}' is not in the allowed list, "
                    "sir. Only safe, read-only commands are permitted."
                ),
            )

        try:
            result = await asyncio.to_thread(
                _run_command, args.command, args.timeout,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Command execution failed: {e}",
            )

        if result.get("success"):
            output = result.get("output", "")
            stderr = result.get("stderr")
            if stderr:
                output += f"\n--- stderr ---\n{stderr}"
            return ToolResult(success=True, output=output or "(no output)")
        return ToolResult(
            success=False,
            error=result.get("error", "Command failed."),
        )
