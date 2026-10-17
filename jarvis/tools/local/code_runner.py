"""Code runner — run Python scripts, tests, and read code files for analysis (T9.4).

A coding-assistant-oriented tool that complements the generic
``CLIRunnerTool``.  While cli_runner whitelists arbitrary safe shell
commands, CodeRunnerTool is specialised for a developer workflow:

- **run_tests**   — ``python -m pytest -q`` inside the workspace.
- **run_script**  — ``python <path>`` with sandbox validation.
- **read_file**   — show a file's content (first 3 000 chars) for LLM analysis.
- **last_errors** — retrieve stderr from the most recent run.

Every execution stores its full stdout+stderr internally so the user can
ask "show errors" immediately after a failing run without re-executing.

Security:
    • All paths are resolved through ``PathValidator`` — no symlink
      escapes, no ``..`` traversal, no access outside the whitelist.
    • Scripts are validated with ``ast.parse`` before execution (syntax
      check only, no eval).
    • Timeouts: 60 s for scripts, 120 s for test suites.
    • No ``shell=True`` — arguments go through ``subprocess.run``
      directly (list form).
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.tools.sandbox import PathValidator, SandboxViolationError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeouts (seconds)
# ---------------------------------------------------------------------------
_SCRIPT_TIMEOUT: int = 60
_TEST_TIMEOUT: int = 120

# ---------------------------------------------------------------------------
# Output limits
# ---------------------------------------------------------------------------
_MAX_OUTPUT_CHARS: int = 4000
_MAX_READ_CHARS: int = 3000


def _truncate(text: str, limit: int) -> str:
    """Truncate *text* to *limit* chars with a notice if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text)} chars total)"


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class CodeRunnerArgs(BaseModel):
    action: Literal["run_script", "run_tests", "read_file", "last_errors"] = Field(
        description=(
            "Action to perform: "
            "run_script — execute a Python script; "
            "run_tests — run pytest in the workspace; "
            "read_file — read a source file for analysis; "
            "last_errors — show stderr from the last run."
        ),
    )
    path: str = Field(
        default="",
        description="Script path (run_script) or file to read (read_file).",
    )
    test_args: str = Field(
        default="",
        description='Extra pytest arguments, e.g. "-k test_login -v".',
    )


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------

class CodeRunnerTool:
    name: str = "code_runner"
    description: str = (
        "Run Python scripts, tests, and read code files for analysis. "
        "Actions: run_script, run_tests, read_file, last_errors. "
        "(RU: запусти тесты, запусти скрипт, прочитай файл, покажи ошибки / "
        "UK: запусти тести, прочитай файл, покажи помилки)"
    )
    args_schema = CodeRunnerArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # -- EN ---------------------------------------------------------------
        VoicePattern(
            regex=r"^run\s+(?:the\s+)?tests?$",
            priority=200,
            args=lambda m: {"action": "run_tests"},
        ),
        VoicePattern(
            regex=r"^run\s+(?:the\s+)?tests?\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "run_tests", "test_args": m.group(1)},
        ),
        VoicePattern(
            regex=r"^run\s+(?:script\s+)?(\S+\.py)$",
            priority=200,
            args=lambda m: {"action": "run_script", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:read|show|cat)\s+(?:file\s+)?(\S+\.py)$",
            priority=250,
            args=lambda m: {"action": "read_file", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:show|what\s+(?:are|were))\s+(?:the\s+)?last\s+errors?$",
            priority=200,
            args=lambda m: {"action": "last_errors"},
        ),
        # -- RU ---------------------------------------------------------------
        VoicePattern(
            regex=r"^запусти\s+тесты$",
            priority=200,
            args=lambda m: {"action": "run_tests"},
        ),
        VoicePattern(
            regex=r"^запусти\s+тесты\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "run_tests", "test_args": m.group(1)},
        ),
        VoicePattern(
            regex=r"^запусти\s+(?:скрипт\s+)?(\S+\.py)$",
            priority=200,
            args=lambda m: {"action": "run_script", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:прочитай|покажи)\s+(?:файл\s+)?(\S+\.py)$",
            priority=250,
            args=lambda m: {"action": "read_file", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:покажи\s+)?(?:последние\s+)?ошибки$",
            priority=200,
            args=lambda m: {"action": "last_errors"},
        ),
        # -- UK ---------------------------------------------------------------
        VoicePattern(
            regex=r"^запусти\s+тести$",
            priority=200,
            args=lambda m: {"action": "run_tests"},
        ),
        VoicePattern(
            regex=r"^запусти\s+тести\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "run_tests", "test_args": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:прочитай|покажи)\s+(?:файл\s+)?(\S+\.py)$",
            priority=250,
            args=lambda m: {"action": "read_file", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:покажи\s+)?(?:останні\s+)?помилки$",
            priority=200,
            args=lambda m: {"action": "last_errors"},
        ),
    )

    # -- Construction -------------------------------------------------------

    def __init__(
        self,
        *,
        path_validator: PathValidator | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self._validator = path_validator
        self._workspace: Path | None = Path(workspace) if workspace else None
        self._last_output: str | None = None
        self._last_stderr: str | None = None

    # -- Helpers ------------------------------------------------------------

    def _resolve_path(self, raw: str) -> Path:
        """Resolve *raw* against the workspace and validate via sandbox.

        Returns an absolute ``Path`` on success; raises
        ``SandboxViolationError`` on any violation.
        """
        p = Path(raw)
        # If relative, anchor to the workspace.
        if not p.is_absolute() and self._workspace is not None:
            p = self._workspace / p
        p = p.resolve()

        # Sandbox validation.
        if self._validator is not None:
            # validate() raises SandboxViolationError on failure and
            # returns the resolved Path on success.
            p = self._validator.validate(str(p))

        return p

    def _cwd(self) -> str | None:
        """Return the working directory for subprocess calls."""
        if self._workspace is not None:
            return str(self._workspace)
        return None

    # -- Action implementations ---------------------------------------------

    async def _run_tests(self, test_args: str) -> ToolResult:
        """Run ``python -m pytest -q [extra_args]`` in the workspace."""
        cmd: list[str] = ["python", "-m", "pytest", "-q"]
        if test_args:
            cmd.extend(shlex.split(test_args))

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=_TEST_TIMEOUT,
                cwd=self._cwd(),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Tests timed out after {_TEST_TIMEOUT}s.",
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error="Python interpreter not found. Is Python installed?",
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        self._last_output = stdout
        self._last_stderr = stderr

        summary = _truncate(stdout, _MAX_OUTPUT_CHARS)
        if result.returncode == 0:
            return ToolResult(success=True, output=summary or "All tests passed.")
        # Non-zero return — tests failed.
        combined = summary
        if stderr:
            combined += "\n\n--- stderr ---\n" + _truncate(stderr, _MAX_OUTPUT_CHARS // 2)
        return ToolResult(success=False, error=combined)

    async def _run_script(self, raw_path: str) -> ToolResult:
        """Run ``python <path>`` after sandbox validation + syntax check."""
        if not raw_path:
            return ToolResult(success=False, error="No script path provided.")

        try:
            resolved = self._resolve_path(raw_path)
        except SandboxViolationError as exc:
            return ToolResult(success=False, error=str(exc))

        if not resolved.exists():
            return ToolResult(
                success=False,
                error=f"File not found: {raw_path}",
            )
        if not resolved.suffix == ".py":
            return ToolResult(
                success=False,
                error=f"Only .py files can be executed: {raw_path}",
            )

        # Syntax check via ast.parse — catch obvious errors before running.
        try:
            source = resolved.read_text(encoding="utf-8")
            ast.parse(source, filename=str(resolved))
        except SyntaxError as exc:
            return ToolResult(
                success=False,
                error=f"Syntax error in {resolved.name}: {exc}",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Could not read {resolved.name}: {exc}",
            )

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["python", str(resolved)],
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT,
                cwd=self._cwd(),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Script timed out after {_SCRIPT_TIMEOUT}s.",
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error="Python interpreter not found.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        self._last_output = stdout
        self._last_stderr = stderr

        if result.returncode == 0:
            return ToolResult(
                success=True,
                output=_truncate(stdout, _MAX_OUTPUT_CHARS) or "(no output)",
            )
        combined = _truncate(stdout, _MAX_OUTPUT_CHARS // 2) if stdout else ""
        if stderr:
            if combined:
                combined += "\n\n--- stderr ---\n"
            combined += _truncate(stderr, _MAX_OUTPUT_CHARS)
        return ToolResult(success=False, error=combined or "Script failed with no output.")

    async def _read_file(self, raw_path: str) -> ToolResult:
        """Read a source file and return first *_MAX_READ_CHARS* chars."""
        if not raw_path:
            return ToolResult(success=False, error="No file path provided.")

        try:
            resolved = self._resolve_path(raw_path)
        except SandboxViolationError as exc:
            return ToolResult(success=False, error=str(exc))

        if not resolved.exists():
            return ToolResult(
                success=False,
                error=f"File not found: {raw_path}",
            )
        if not resolved.is_file():
            return ToolResult(
                success=False,
                error=f"Not a file: {raw_path}",
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(success=False, error=f"Could not read file: {exc}")

        total_lines = content.count("\n") + 1
        truncated = _truncate(content, _MAX_READ_CHARS)
        header = f"# {resolved.name} ({total_lines} lines)\n\n"
        return ToolResult(success=True, output=header + truncated)

    async def _last_errors(self) -> ToolResult:
        """Return stderr from the last run_tests or run_script call."""
        if self._last_stderr:
            return ToolResult(
                success=True,
                output=_truncate(self._last_stderr, _MAX_OUTPUT_CHARS),
            )
        if self._last_output is not None:
            return ToolResult(
                success=True,
                output="No errors in the last run. Last output:\n"
                + _truncate(self._last_output, _MAX_OUTPUT_CHARS // 2),
            )
        return ToolResult(
            success=False,
            error="No previous run to show errors from.",
        )

    # -- Dispatch -----------------------------------------------------------

    async def execute(self, args: CodeRunnerArgs) -> ToolResult:
        if args.action == "run_tests":
            return await self._run_tests(args.test_args)
        if args.action == "run_script":
            return await self._run_script(args.path)
        if args.action == "read_file":
            return await self._read_file(args.path)
        if args.action == "last_errors":
            return await self._last_errors()
        return ToolResult(success=False, error=f"Unknown action: {args.action}")
