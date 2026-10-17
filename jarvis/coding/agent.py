"""CodingAgent — full-featured coding assistant powered by a dedicated LLM.

The agent receives a natural-language coding task, breaks it into steps via
the LLM, and executes each step (create files, edit files, run commands,
run tests, fix errors, git operations).  It streams progress strings that
the voice layer / UI can relay to the user.

Security:
    * All file writes are sandboxed — the agent cannot create or modify
      files outside its ``workspace`` directory.
    * Shell commands run with a timeout and capture output.
    * ``ast.parse`` is applied to every generated Python file before it
      is written to disk (syntax gate).

Usage::

    from jarvis.coding.agent import CodingAgent
    from jarvis.llm.model_provider import ModelProvider

    provider = ModelProvider.from_config(coding_config, ollama_client)
    agent = CodingAgent(provider=provider, workspace=Path("./my_project"),
                        config=coding_config)
    async for message in agent.execute_task("Create a Flask REST API"):
        print(message)
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import sys
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.core.config import CodingConfig
    from jarvis.llm.model_provider import ChatMessage, ChatResponse, ModelProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CODING_SYSTEM_PROMPT = (
    "You are a senior Python developer assistant. You write clean, "
    "production-ready code.\n"
    "\n"
    "Rules:\n"
    "1. Write complete, working code — no placeholders, no \"TODO\", no \"...\".\n"
    "2. Include proper error handling, type hints, and docstrings.\n"
    "3. Follow PEP 8 style.\n"
    "4. If editing existing code, preserve the original structure and only "
    "change what's needed.\n"
    "5. If creating a project, include: __init__.py, requirements.txt, "
    "README.md, .gitignore.\n"
    "6. When fixing bugs, explain what was wrong before fixing.\n"
    "\n"
    "Response format for file creation:\n"
    "```filepath: path/to/file.py\n"
    "<complete file content>\n"
    "```\n"
    "\n"
    "Response format for edits:\n"
    "```edit: path/to/file.py\n"
    "<complete new file content>\n"
    "```\n"
)

_CMD_TIMEOUT: int = 60
_TEST_TIMEOUT: int = 120
_MAX_OUTPUT_CHARS: int = 4000


# ---------------------------------------------------------------------------
# Response parser — extracts file blocks from LLM output
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FileBlock:
    """A single file block extracted from an LLM response."""

    path: str
    content: str
    action: str  # "create" or "edit"


class ResponseParser:
    """Parse LLM responses that contain fenced code blocks with file paths.

    Recognises two patterns::

        ```filepath: path/to/file.py
        <content>
        ```

        ```edit: path/to/file.py
        <content>
        ```

    Also handles bare code fences with a path comment on the first line.
    """

    # Matches  ```<optional-lang> filepath: <path>  or  ```<optional-lang> edit: <path>
    _BLOCK_RE = re.compile(
        r"```(?:\w+)?\s*(?P<action>filepath|edit):\s*(?P<path>\S+)\s*\n"
        r"(?P<content>.*?)\n```",
        re.DOTALL,
    )

    @classmethod
    def parse(cls, text: str) -> list[FileBlock]:
        """Extract all file blocks from *text*.

        Returns a list of ``FileBlock`` objects in declaration order.
        Blocks with empty content are silently skipped.
        """
        blocks: list[FileBlock] = []
        for match in cls._BLOCK_RE.finditer(text):
            action_raw = match.group("action").strip().lower()
            path = match.group("path").strip()
            content = match.group("content")
            if not path or not content:
                continue
            action = "edit" if action_raw == "edit" else "create"
            blocks.append(FileBlock(path=path, content=content, action=action))
        return blocks


# ---------------------------------------------------------------------------
# Plan step schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step in the agent's execution plan."""

    action: str  # "create_file", "edit_file", "run_command", "run_tests", "git"
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CodingAgent
# ---------------------------------------------------------------------------

class CodingAgent:
    """Full-featured coding assistant that can create projects, edit files,
    run tests, fix bugs, and manage git — powered by a dedicated coding model.

    Uses ``ModelProvider`` (can be local Ollama or cloud API like GPT-4o / Claude).
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        workspace: Path,
        config: CodingConfig,
    ) -> None:
        from jarvis.llm.model_provider import ChatMessage as _CM

        self._provider = provider
        self._workspace = Path(workspace).resolve()
        self._config = config
        self._history: list[_CM] = []
        self._ChatMessage = _CM

    # -- public entry point ------------------------------------------------

    async def execute_task(self, task: str) -> AsyncIterator[str]:
        """Main entry — receive a coding task, plan and execute it.

        Yields progress messages as strings for TTS / UI.

        Examples::

            "Create a Python project for news parsing"
            "Add authentication to the current project"
            "Fix the failing tests"
            "Commit and push to GitHub"
        """
        yield "Planning the task, sir..."

        try:
            steps = await self._plan(task)
        except Exception as exc:
            log.exception("planning failed for task %r", task)
            yield f"Planning failed: {exc}"
            return

        if not steps:
            yield "I couldn't break this task into actionable steps."
            return

        yield f"Plan ready — {len(steps)} step(s). Executing..."

        for i, step in enumerate(steps, 1):
            action = step.action
            details = step.details

            if action == "create_file":
                path = details.get("path", "")
                description = details.get("description", task)
                yield f"Creating {path or 'file'}..."
                try:
                    result = await self._create_file(path, description)
                    yield result
                except Exception as exc:
                    yield f"Error creating file: {exc}"

            elif action == "edit_file":
                path = details.get("path", "")
                instruction = details.get("instruction", task)
                yield f"Editing {path or 'file'}..."
                try:
                    result = await self._edit_file(path, instruction)
                    yield result
                except Exception as exc:
                    yield f"Error editing file: {exc}"

            elif action == "run_command":
                command = details.get("command", "")
                yield f"Running: {command}..."
                try:
                    stdout, stderr, rc = await self._run_command(command)
                    if rc == 0:
                        yield f"Command succeeded."
                    else:
                        yield f"Command failed (exit {rc}): {stderr or stdout}"
                except Exception as exc:
                    yield f"Command error: {exc}"

            elif action == "run_tests":
                yield "Running tests..."
                try:
                    success, output = await self._run_tests()
                    if success:
                        yield "All tests passed."
                    else:
                        yield f"Tests failed. Attempting to fix..."
                        fixed = await self._fix_errors(output)
                        if fixed:
                            yield "Errors fixed, tests pass now."
                        else:
                            yield "Could not fix all errors automatically."
                except Exception as exc:
                    yield f"Test error: {exc}"

            elif action == "git":
                git_action = details.get("git_action", "commit")
                yield f"Git: {git_action}..."
                try:
                    result = await self._git_operation(
                        git_action, **{k: v for k, v in details.items() if k != "git_action"},
                    )
                    yield result
                except Exception as exc:
                    yield f"Git error: {exc}"

            elif action == "create_project":
                description = details.get("description", task)
                yield "Scaffolding project..."
                try:
                    await self._create_project(description)
                    yield "Project scaffolded."
                except Exception as exc:
                    yield f"Project creation error: {exc}"

            else:
                yield f"Unknown step action: {action}"

        # Auto-commit if configured.
        if self._config.auto_commit:
            yield "Auto-committing changes..."
            try:
                result = await self._git_operation("commit", message=f"auto: {task}")
                yield result
            except Exception as exc:
                yield f"Auto-commit failed: {exc}"

        yield "Task complete, sir."

    # -- planning ----------------------------------------------------------

    async def _plan(self, task: str) -> list[PlanStep]:
        """Ask the coding model to break the task into steps.

        Returns a list of ``PlanStep`` objects.
        """
        from jarvis.llm.model_provider import ChatMessage

        prompt = (
            f"Break this coding task into concrete steps. "
            f"Return a JSON array where each element has:\n"
            f'  "action": one of "create_file", "edit_file", "run_command", '
            f'"run_tests", "git", "create_project"\n'
            f'  "details": object with action-specific keys\n\n'
            f"For create_file: {{\"path\": \"...\", \"description\": \"...\"}}\n"
            f"For edit_file: {{\"path\": \"...\", \"instruction\": \"...\"}}\n"
            f"For run_command: {{\"command\": \"...\"}}\n"
            f"For run_tests: {{}}\n"
            f"For git: {{\"git_action\": \"init|add|commit|push\", \"message\": \"...\"}}\n"
            f"For create_project: {{\"description\": \"...\"}}\n\n"
            f"Respond ONLY with the JSON array, no extra text.\n\n"
            f"Task: {task}"
        )

        messages = [
            ChatMessage(role="system", content=CODING_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages, temperature=0.2)
        raw = response.content.strip()

        # Strip markdown fences if present.
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON plan: %s", raw[:500])
            return []

        if not isinstance(data, list):
            log.warning("LLM plan is not a list: %s", type(data))
            return []

        steps: list[PlanStep] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "")
            details = item.get("details", {})
            if not isinstance(details, dict):
                details = {}
            if action:
                steps.append(PlanStep(action=action, details=details))

        self._history = messages + [
            ChatMessage(role="assistant", content=response.content),
        ]

        return steps

    # -- file creation -----------------------------------------------------

    def _validate_sandbox(self, path: str) -> Path:
        """Resolve *path* against workspace and ensure it stays inside.

        Raises ``ValueError`` if the resolved path escapes the workspace.
        """
        # Normalise separators.
        cleaned = path.replace("\\", "/")
        # Reject paths that start with / (Unix absolute) before stripping.
        if cleaned.startswith("/"):
            raise ValueError(
                f"Absolute paths are not allowed: {path!r}"
            )
        candidate = Path(cleaned)
        # Reject Windows-style absolute paths (e.g. C:\...).
        if candidate.is_absolute():
            raise ValueError(
                f"Absolute paths are not allowed: {path!r}"
            )
        resolved = (self._workspace / candidate).resolve()
        # Ensure it is inside the workspace.
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            raise ValueError(
                f"Path {path!r} escapes the workspace ({self._workspace})"
            )
        return resolved

    async def _create_file(self, path: str, description: str) -> str:
        """Ask model to generate file content, write to disk."""
        from jarvis.llm.model_provider import ChatMessage

        prompt = (
            f"Create the file `{path}` with the following purpose:\n"
            f"{description}\n\n"
            f"Respond with a single fenced code block:\n"
            f"```filepath: {path}\n"
            f"<complete file content>\n"
            f"```"
        )

        messages = self._history + [
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages, temperature=0.2, max_tokens=4096)

        blocks = ResponseParser.parse(response.content)
        if not blocks:
            # Fallback: treat entire response as content.
            content = response.content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```\w*\n?", "", content)
                content = re.sub(r"\n?```\s*$", "", content)
            return await self._write_file(path, content)

        results: list[str] = []
        for block in blocks:
            result = await self._write_file(block.path, block.content)
            results.append(result)

        self._history = messages + [
            ChatMessage(role="assistant", content=response.content),
        ]

        return "; ".join(results)

    async def _write_file(self, path: str, content: str) -> str:
        """Write content to disk after sandbox validation and syntax check."""
        resolved = self._validate_sandbox(path)

        # Ensure parent directories exist.
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Syntax check for Python files.
        if resolved.suffix == ".py":
            try:
                ast.parse(content, filename=str(resolved))
            except SyntaxError as exc:
                return f"Syntax error in {path}: {exc}"

        await asyncio.to_thread(resolved.write_text, content, encoding="utf-8")
        return f"Created {path} ({len(content)} chars)"

    # -- file editing ------------------------------------------------------

    async def _edit_file(self, path: str, instruction: str) -> str:
        """Read file, ask model to edit it, write back."""
        from jarvis.llm.model_provider import ChatMessage

        resolved = self._validate_sandbox(path)
        if not resolved.exists():
            return f"File not found: {path}"

        try:
            original = await asyncio.to_thread(resolved.read_text, "utf-8")
        except Exception as exc:
            return f"Cannot read {path}: {exc}"

        prompt = (
            f"Edit the file `{path}` according to this instruction:\n"
            f"{instruction}\n\n"
            f"Current content:\n"
            f"```\n{original}\n```\n\n"
            f"Respond with the complete updated file in a fenced block:\n"
            f"```edit: {path}\n"
            f"<complete new file content>\n"
            f"```"
        )

        messages = self._history + [
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(messages, temperature=0.2, max_tokens=4096)

        blocks = ResponseParser.parse(response.content)
        if not blocks:
            return f"Model did not return an editable block for {path}."

        # Use the first matching block.
        block = blocks[0]
        result = await self._write_file(block.path, block.content)

        self._history = messages + [
            ChatMessage(role="assistant", content=response.content),
        ]

        return result

    # -- shell commands ----------------------------------------------------

    async def _run_command(self, command: str) -> tuple[str, str, int]:
        """Run a shell command, return (stdout, stderr, returncode).

        The command runs with ``shell=False`` (split via shlex) inside the
        workspace directory.  A 60-second timeout is enforced.
        """
        import shlex

        try:
            args = shlex.split(command)
        except ValueError as exc:
            return ("", f"Bad command: {exc}", 1)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                args,
                capture_output=True,
                text=True,
                timeout=_CMD_TIMEOUT,
                cwd=str(self._workspace),
            )
        except subprocess.TimeoutExpired:
            return ("", f"Command timed out after {_CMD_TIMEOUT}s", 1)
        except FileNotFoundError:
            return ("", f"Command not found: {args[0]}", 1)
        except OSError as exc:
            return ("", str(exc), 1)

        stdout = result.stdout.strip()[:_MAX_OUTPUT_CHARS]
        stderr = result.stderr.strip()[:_MAX_OUTPUT_CHARS]
        return (stdout, stderr, result.returncode)

    # -- tests -------------------------------------------------------------

    async def _run_tests(self) -> tuple[bool, str]:
        """Run pytest, return (success, output)."""
        cmd = [sys.executable, "-m", "pytest", "-q"]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=_TEST_TIMEOUT,
                cwd=str(self._workspace),
            )
        except subprocess.TimeoutExpired:
            return (False, f"Tests timed out after {_TEST_TIMEOUT}s")
        except FileNotFoundError:
            return (False, "Python interpreter not found")
        except OSError as exc:
            return (False, str(exc))

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        output = stdout
        if stderr:
            output += "\n--- stderr ---\n" + stderr

        output = output[:_MAX_OUTPUT_CHARS]
        return (result.returncode == 0, output)

    # -- error fixing loop -------------------------------------------------

    async def _fix_errors(self, test_output: str) -> bool:
        """Read test errors, ask model to fix, return True if fixed.

        Iterates up to ``config.max_iterations`` times.
        """
        from jarvis.llm.model_provider import ChatMessage

        for attempt in range(1, self._config.max_iterations + 1):
            log.info("fix attempt %d / %d", attempt, self._config.max_iterations)

            prompt = (
                f"The tests failed with the following output:\n\n"
                f"```\n{test_output}\n```\n\n"
                f"Analyse the errors and provide fixes. For each file that "
                f"needs changing, respond with:\n"
                f"```edit: path/to/file.py\n"
                f"<complete new file content>\n"
                f"```"
            )

            messages = self._history + [
                ChatMessage(role="user", content=prompt),
            ]

            response = await self._provider.chat(
                messages, temperature=0.2, max_tokens=4096,
            )

            blocks = ResponseParser.parse(response.content)
            if not blocks:
                log.warning("fix attempt %d: no editable blocks returned", attempt)
                return False

            for block in blocks:
                try:
                    await self._write_file(block.path, block.content)
                except Exception as exc:
                    log.warning("fix attempt %d: write failed for %s: %s",
                                attempt, block.path, exc)

            self._history = messages + [
                ChatMessage(role="assistant", content=response.content),
            ]

            # Re-run tests.
            success, test_output = await self._run_tests()
            if success:
                return True

        return False

    # -- git operations ----------------------------------------------------

    async def _git_operation(self, action: str, **kwargs: Any) -> str:
        """Git operations: init, add, commit, push.

        Delegates to subprocess git calls inside the workspace.
        """
        cwd = str(self._workspace)

        if action == "init":
            return await self._git_run(["git", "init"], cwd)

        if action == "add":
            return await self._git_run(["git", "add", "-A"], cwd)

        if action == "commit":
            message = kwargs.get("message", "auto-commit by coding agent")
            # Stage everything first.
            await self._git_run(["git", "add", "-A"], cwd)
            return await self._git_run(["git", "commit", "-m", message], cwd)

        if action == "push":
            return await self._git_run(["git", "push"], cwd)

        return f"Unknown git action: {action}"

    async def _git_run(self, cmd: list[str], cwd: str) -> str:
        """Run a git command and return a human-readable result string."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return f"Git timed out: {' '.join(cmd)}"
        except FileNotFoundError:
            return "git is not installed or not on PATH"
        except OSError as exc:
            return f"Git error: {exc}"

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return f"Git failed: {error}"

        output = result.stdout.strip() or result.stderr.strip()
        return output or f"git {cmd[1]} OK"

    # -- project scaffolding -----------------------------------------------

    async def _create_project(self, description: str) -> None:
        """Scaffold a new project from description.

        Asks the model for a full project structure and writes all files.
        """
        from jarvis.llm.model_provider import ChatMessage

        prompt = (
            f"Create a complete Python project based on this description:\n"
            f"{description}\n\n"
            f"Include at minimum:\n"
            f"- __init__.py\n"
            f"- main.py (entry point)\n"
            f"- requirements.txt\n"
            f"- README.md\n"
            f"- .gitignore\n"
            f"- At least one test file in tests/\n\n"
            f"For each file, use the format:\n"
            f"```filepath: path/to/file\n"
            f"<complete content>\n"
            f"```"
        )

        messages = [
            ChatMessage(role="system", content=CODING_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]

        response = await self._provider.chat(
            messages, temperature=0.2, max_tokens=8192,
        )

        blocks = ResponseParser.parse(response.content)
        for block in blocks:
            try:
                await self._write_file(block.path, block.content)
            except Exception as exc:
                log.warning("project scaffold: failed to write %s: %s",
                            block.path, exc)

        self._history = messages + [
            ChatMessage(role="assistant", content=response.content),
        ]
