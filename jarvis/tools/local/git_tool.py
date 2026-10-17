"""Git version control tool — status, diff, commit, push, pull, branch, log.

Voice-controlled git operations with per-action risk gating:
  - READ actions (status, diff, log): execute immediately.
  - WRITE actions (add, pull, branch, checkout): execute, audit-logged.
  - DESTRUCTIVE actions (commit, push): require user confirmation via
    the standard should_confirm() / risk.py gate.

Auto-detects the nearest .git parent from CWD or config workspace path
when no explicit path is given.

Responses adapt to the conversation language (EN/RU/UK) where feasible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


def _find_git_root(start: str | None = None) -> str | None:
    """Walk up from *start* (or CWD) looking for a .git directory.

    Returns the absolute path of the repository root, or None if no
    .git ancestor is found within 20 levels (safety cap).
    """
    if start:
        current = Path(start).resolve()
    else:
        current = Path.cwd().resolve()

    for _ in range(20):
        if (current / ".git").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _run_git(
    args: list[str],
    cwd: str,
    *,
    timeout: int = 30,
) -> dict:
    """Run a git sub-command and return structured output.

    Returns ``{"success": True, "stdout": ..., "stderr": ...}`` on a
    zero exit code, or ``{"success": False, "error": ...}`` on failure.
    """
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Truncate very long output.
        max_len = 4000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + f"\n... (truncated, {len(stdout)} chars total)"

        if result.returncode != 0:
            error_msg = stderr or stdout or f"git exited with code {result.returncode}"
            return {"success": False, "error": error_msg}
        return {"success": True, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"git command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"success": False, "error": "git is not installed or not on PATH"}
    except OSError as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Pydantic args model
# ---------------------------------------------------------------------------

class GitArgs(BaseModel):
    action: Literal[
        "status", "diff", "log", "commit", "push", "pull",
        "branch", "checkout", "add",
    ] = Field(
        description=(
            "The git action to perform: status, diff, log, commit, "
            "push, pull, branch, checkout, add."
        ),
    )
    message: str = Field(
        default="",
        description="Commit message (required for 'commit' action).",
    )
    branch: str = Field(
        default="",
        description="Branch name (required for 'branch' and 'checkout' actions).",
    )
    path: str = Field(
        default="",
        description=(
            "Working directory for git commands. "
            "If empty, auto-detects nearest .git parent from CWD."
        ),
    )


# ---------------------------------------------------------------------------
# Actions that require user confirmation (destructive / remote-push).
# These are gated via RiskLevel.DESTRUCTIVE in risk.py's TOOL_RISK_MAP,
# but the tool also carries a per-action confirmation_summary() hook so
# the dialog tells the user *what* git is about to do.
# ---------------------------------------------------------------------------

_CONFIRM_ACTIONS: frozenset[str] = frozenset({"commit", "push"})


class GitTool:
    """Git version control: status, diff, commit, push, pull, branch, log.

    (RU: гит статус, что изменилось, закоммить, запушь /
     UK: гіт статус, що змінилось, закомміть, запуш)
    """

    name: str = "git_tool"
    description: str = (
        "Git version control: status, diff, commit, push, pull, branch, log. "
        "READ actions (status/diff/log) execute immediately; "
        "commit and push require confirmation. "
        "(RU: гит статус, коммиты, что изменилось, закоммить, запушь / "
        "UK: гіт статус, коміти, що змінилось, закомміть, запуш)"
    )
    args_schema = GitArgs
    requires_confirmation: bool = False  # per-action via risk.py TOOL_RISK_MAP

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # -- EN ----------------------------------------------------------
        VoicePattern(
            regex=r"^git\s+status$",
            priority=200,
            args=lambda m: {"action": "status"},
        ),
        VoicePattern(
            regex=r"^git\s+diff$",
            priority=200,
            args=lambda m: {"action": "diff"},
        ),
        VoicePattern(
            regex=r"^git\s+log$",
            priority=200,
            args=lambda m: {"action": "log"},
        ),
        VoicePattern(
            regex=r"^(?:git\s+)?commit\s+(?:with\s+message\s+)?[\"']?(.+?)[\"']?$",
            priority=200,
            args=lambda m: {"action": "commit", "message": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:git\s+)?push$",
            priority=200,
            args=lambda m: {"action": "push"},
        ),
        VoicePattern(
            regex=r"^(?:git\s+)?pull$",
            priority=200,
            args=lambda m: {"action": "pull"},
        ),
        VoicePattern(
            regex=r"^(?:create|new)\s+branch\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "branch", "branch": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:switch|checkout)\s+(?:to\s+)?(?:branch\s+)?(.+)$",
            priority=250,
            args=lambda m: {"action": "checkout", "branch": m.group(1)},
        ),
        # -- RU ----------------------------------------------------------
        VoicePattern(
            regex=r"^(?:гит\s+)?статус$",
            priority=200,
            args=lambda m: {"action": "status"},
        ),
        VoicePattern(
            regex=r"^(?:покажи\s+)?(?:что\s+)?изменилось$",
            priority=200,
            args=lambda m: {"action": "diff"},
        ),
        VoicePattern(
            regex=r"^(?:покажи\s+)?(?:последние\s+)?коммиты$",
            priority=200,
            args=lambda m: {"action": "log"},
        ),
        VoicePattern(
            regex=r"^(?:за)?коммить?\s+(?:с?\s*сообщением?\s+)?(.+)$",
            priority=200,
            args=lambda m: {"action": "commit", "message": m.group(1)},
        ),
        VoicePattern(
            regex=r"^запушь?$",
            priority=200,
            args=lambda m: {"action": "push"},
        ),
        VoicePattern(
            regex=r"^(?:git\s+)?пулл?$",
            priority=200,
            args=lambda m: {"action": "pull"},
        ),
        VoicePattern(
            regex=r"^создай\s+ветку\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "branch", "branch": m.group(1)},
        ),
        VoicePattern(
            regex=r"^переключись\s+на\s+ветку\s+(.+)$",
            priority=250,
            args=lambda m: {"action": "checkout", "branch": m.group(1)},
        ),
        # -- UK ----------------------------------------------------------
        VoicePattern(
            regex=r"^(?:гіт\s+)?статус$",
            priority=200,
            args=lambda m: {"action": "status"},
        ),
        VoicePattern(
            regex=r"^(?:покажи\s+)?що\s+змінилось$",
            priority=200,
            args=lambda m: {"action": "diff"},
        ),
        VoicePattern(
            regex=r"^(?:за)?комміть?\s+(?:з?\s*повідомленням?\s+)?(.+)$",
            priority=200,
            args=lambda m: {"action": "commit", "message": m.group(1)},
        ),
        VoicePattern(
            regex=r"^запуш$",
            priority=200,
            args=lambda m: {"action": "push"},
        ),
        VoicePattern(
            regex=r"^створи\s+гілку\s+(.+)$",
            priority=200,
            args=lambda m: {"action": "branch", "branch": m.group(1)},
        ),
        VoicePattern(
            regex=r"^переключись\s+на\s+гілку\s+(.+)$",
            priority=250,
            args=lambda m: {"action": "checkout", "branch": m.group(1)},
        ),
    )

    def __init__(self, *, workspace_path: str | None = None) -> None:
        """Optionally set a default workspace path for .git auto-detection."""
        self._workspace_path = workspace_path

    # -- public helpers for the confirmation dialog ---------------------

    def confirmation_summary(self, args: GitArgs) -> str:
        """Human-readable summary shown in the confirmation dialog."""
        if args.action == "commit":
            msg = args.message or "(no message)"
            return f'Git commit with message: "{msg}"'
        if args.action == "push":
            return "Git push — send local commits to the remote repository"
        return self.description

    # -- execution -----------------------------------------------------

    async def execute(self, args: GitArgs) -> ToolResult:
        # Resolve working directory.
        cwd = args.path or _find_git_root(self._workspace_path)
        if cwd is None:
            return ToolResult(
                success=False,
                error=(
                    "No git repository found. Please specify a path or "
                    "navigate to a directory with a .git folder."
                ),
            )

        action = args.action

        if action == "status":
            return await self._status(cwd)
        elif action == "diff":
            return await self._diff(cwd)
        elif action == "log":
            return await self._log(cwd)
        elif action == "add":
            return await self._add(cwd)
        elif action == "commit":
            return await self._commit(cwd, args.message)
        elif action == "push":
            return await self._push(cwd)
        elif action == "pull":
            return await self._pull(cwd)
        elif action == "branch":
            return await self._branch(cwd, args.branch)
        elif action == "checkout":
            return await self._checkout(cwd, args.branch)
        else:
            return ToolResult(success=False, error=f"Unknown git action: {action}")

    # -- individual actions --------------------------------------------

    async def _status(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["status", "--short"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stdout = res["stdout"]
        if not stdout:
            return ToolResult(success=True, output="Working tree clean — no changes.")
        return ToolResult(success=True, output=stdout)

    async def _diff(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["diff", "--stat"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stdout = res["stdout"]
        if not stdout:
            return ToolResult(success=True, output="No changes.")
        return ToolResult(success=True, output=stdout)

    async def _log(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["log", "--oneline", "-10"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stdout = res["stdout"]
        if not stdout:
            return ToolResult(success=True, output="No commits yet.")
        return ToolResult(success=True, output=stdout)

    async def _add(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["add", "-A"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        return ToolResult(success=True, output="All changes staged (git add -A).")

    async def _commit(self, cwd: str, message: str) -> ToolResult:
        if not message.strip():
            return ToolResult(
                success=False,
                error="Commit message is required. Please provide a message.",
            )
        # Stage all first.
        add_res = await asyncio.to_thread(_run_git, ["add", "-A"], cwd)
        if not add_res["success"]:
            return ToolResult(success=False, error=f"git add failed: {add_res['error']}")
        # Commit.
        commit_res = await asyncio.to_thread(
            _run_git, ["commit", "-m", message.strip()], cwd,
        )
        if not commit_res["success"]:
            return ToolResult(success=False, error=commit_res["error"])
        return ToolResult(success=True, output=commit_res["stdout"])

    async def _push(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["push"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stdout = res["stdout"] or res.get("stderr", "")
        return ToolResult(
            success=True,
            output=stdout or "Pushed successfully.",
        )

    async def _pull(self, cwd: str) -> ToolResult:
        res = await asyncio.to_thread(_run_git, ["pull"], cwd)
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stdout = res["stdout"]
        return ToolResult(success=True, output=stdout or "Already up to date.")

    async def _branch(self, cwd: str, branch: str) -> ToolResult:
        if not branch.strip():
            return ToolResult(
                success=False,
                error="Branch name is required.",
            )
        res = await asyncio.to_thread(
            _run_git, ["checkout", "-b", branch.strip()], cwd,
        )
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stderr = res.get("stderr", "")
        return ToolResult(
            success=True,
            output=stderr or res["stdout"] or f"Created and switched to branch '{branch.strip()}'.",
        )

    async def _checkout(self, cwd: str, branch: str) -> ToolResult:
        if not branch.strip():
            return ToolResult(
                success=False,
                error="Branch name is required.",
            )
        res = await asyncio.to_thread(
            _run_git, ["checkout", branch.strip()], cwd,
        )
        if not res["success"]:
            return ToolResult(success=False, error=res["error"])
        stderr = res.get("stderr", "")
        return ToolResult(
            success=True,
            output=stderr or res["stdout"] or f"Switched to branch '{branch.strip()}'.",
        )
