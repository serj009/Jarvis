"""CodingAgent — full-featured coding assistant powered by a dedicated LLM.

The agent receives a natural-language coding task, breaks it into steps via
the LLM, and executes each step (create files, edit files, run commands,
run tests, fix errors, git operations).  It streams progress strings that
the voice layer / UI can relay to the user.

For large projects (>3 000 source lines) the agent automatically activates
three support modules so it never overflows the model's context window:

  * **ProjectMap** — compact tree overview of the codebase (500-1000 tokens).
  * **CodeSearch** — symbol index + grep-like search + single-function
    extraction so the model reads only what it needs.
  * **AgentNotepad** — persistent notes/decisions/file summaries that are
    injected into every prompt, keeping the model oriented across steps.

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
    from jarvis.coding.agent_notepad import AgentNotepad
    from jarvis.coding.voice_confirm import VoiceConfirm
    from jarvis.coding.code_search import CodeSearch
    from jarvis.coding.project_map import ProjectMap
    from jarvis.core.config import CodingConfig
    from jarvis.llm.model_provider import ChatMessage, ChatResponse, ModelProvider

from jarvis.coding.languages import (
    LANGUAGES,
    detect_from_description,
    detect_language,
    get_language_config,
    get_language_for_extension,
)

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

_LARGE_PROJECT_THRESHOLD: int = 3000  # lines — above this, use map+search

_CMD_TIMEOUT: int = 60
_TEST_TIMEOUT: int = 120
_MAX_OUTPUT_CHARS: int = 4000


def _make_system_prompt(lang_config) -> str:
    """Generate a language-aware coding system prompt.

    For Python this returns the original ``CODING_SYSTEM_PROMPT`` so
    existing behaviour is unchanged.  For other languages a tailored
    prompt with language-specific conventions is produced.
    """
    if lang_config.name == "python":
        return CODING_SYSTEM_PROMPT

    return (
        f"You are a senior {lang_config.display_name} developer assistant. "
        f"You write clean, production-ready code.\n"
        f"\n"
        f"Rules:\n"
        f"1. Write complete, working code — no placeholders, no \"TODO\", no \"...\".\n"
        f"2. Include proper error handling and documentation.\n"
        f"3. {lang_config.system_prompt_rules}\n"
        f"4. If editing existing code, preserve the original structure and only "
        f"change what's needed.\n"
        f"5. When fixing bugs, explain what was wrong before fixing.\n"
        f"\n"
        f"Response format for file creation:\n"
        f"```filepath: path/to/file\n"
        f"<complete file content>\n"
        f"```\n"
        f"\n"
        f"Response format for edits:\n"
        f"```edit: path/to/file\n"
        f"<complete new file content>\n"
        f"```\n"
    )

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

    For large projects the agent transparently activates:
      * ``ProjectMap`` — compact codebase overview injected into planning.
      * ``CodeSearch`` — symbol index + targeted reads.
      * ``AgentNotepad`` — persistent memory across steps.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        workspace: Path,
        config: CodingConfig,
        confirm: VoiceConfirm | None = None,
    ) -> None:
        from jarvis.llm.model_provider import ChatMessage as _CM

        self._provider = provider
        self._workspace = Path(workspace).resolve()
        self._config = config
        self._history: list[_CM] = []
        self._language: str = ""  # Auto-detected or from config
        self._ChatMessage = _CM
        self._confirm: VoiceConfirm | None = confirm

        # Large-project tools (lazily initialised).
        self._notepad: AgentNotepad | None = None
        self._project_map: ProjectMap | None = None
        self._code_search: CodeSearch | None = None
        self._large_project: bool = False

    # -- large-project helpers --------------------------------------------

    def _init_large_project_tools(self) -> None:
        """Lazily initialise ProjectMap, CodeSearch, and AgentNotepad."""
        from jarvis.coding.agent_notepad import AgentNotepad
        from jarvis.coding.code_search import CodeSearch
        from jarvis.coding.project_map import ProjectMap

        if self._notepad is None:
            self._notepad = AgentNotepad()
        if self._project_map is None:
            self._project_map = ProjectMap(self._workspace)
        if self._code_search is None:
            self._code_search = CodeSearch(self._workspace)
            self._code_search.build_index()

    def _estimate_project_lines(self) -> int:
        """Quickly estimate total lines of source code in the workspace."""
        total = 0
        source_exts = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs",
            ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc",
        }
        skip = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "env", "target", "bin", "obj", "dist", "build",
        }
        for dirpath, dirnames, filenames in os.walk(self._workspace):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in source_exts:
                    try:
                        with open(
                            os.path.join(dirpath, fname),
                            encoding="utf-8",
                            errors="replace",
                        ) as fh:
                            total += sum(1 for _ in fh)
                    except OSError:
                        pass
        return total

    def _build_system_prompt(self) -> str:
        """Construct the system prompt, appending project map and notepad
        context for large projects."""
        system = self._get_system_prompt()

        if self._large_project and self._project_map is not None:
            project_map_text = self._project_map.generate()
            system += (
                "\n\n--- PROJECT MAP ---\n"
                + project_map_text
                + "\n--- END PROJECT MAP ---\n"
            )

        if self._notepad is not None and not self._notepad.is_empty:
            system += (
                "\n\n--- AGENT MEMORY ---\n"
                + self._notepad.to_context()
                + "\n--- END AGENT MEMORY ---\n"
            )

        return system

    # -- voice confirmation helper ----------------------------------------

    async def _ask_confirm(self, action_description: str) -> bool:
        """Ask for voice confirmation if a VoiceConfirm instance is wired.

        Returns ``True`` when the user confirmed **or** when no
        ``VoiceConfirm`` is configured (backward-compatible default).
        """
        if self._confirm is None:
            return True
        return await self._confirm.confirm(action_description)

    def _confirm_language(self) -> str:
        """Map the detected project language to a UI language code.

        Defaults to ``"en"`` — override in subclasses or configuration.
        """
        return "en"

    # -- public entry point ------------------------------------------------
    # -- language helpers --------------------------------------------------

    def _get_language(self) -> str:
        """Return the detected language, caching the result.

        Priority: explicit ``self._language`` → config ``preferred_language``
        → auto-detect from workspace files → ``"python"`` default.
        """
        if self._language:
            return self._language

        preferred = getattr(self._config, "preferred_language", "")
        if preferred and preferred in LANGUAGES:
            self._language = preferred
            return self._language

        self._language = detect_language(self._workspace)
        return self._language

    def _get_system_prompt(self) -> str:
        """Return a language-appropriate system prompt.

        For Python the original ``CODING_SYSTEM_PROMPT`` is used (backward
        compatible).  For other languages a tailored prompt is built via
        :func:`_make_system_prompt`.
        """
        lang = self._get_language()
        lang_config = get_language_config(lang)
        return _make_system_prompt(lang_config)

    # -- public entry point (cont.) ----------------------------------------

    async def execute_task(self, task: str) -> AsyncIterator[str]:
        """Main entry — receive a coding task, plan and execute it.

        Yields progress messages as strings for TTS / UI.  For large
        projects (>3 000 lines) the agent automatically uses ProjectMap,
        CodeSearch, and AgentNotepad to stay within context limits.

        Examples::

            "Create a Python project for news parsing"
            "Add authentication to the current project"
            "Fix the failing tests"
            "Commit and push to GitHub"
        """
        yield "Planning the task, sir..."

        # Auto-detect project language from task description / workspace.
        if not self._language:
            preferred = getattr(self._config, "preferred_language", "")
            if preferred and preferred in LANGUAGES:
                self._language = preferred
            else:
                self._language = detect_language(self._workspace)
                # If workspace has no strong signal, try task description.
                if self._language == "python":
                    desc_lang = detect_from_description(task)
                    if desc_lang != "python":
                        self._language = desc_lang

        # Detect large projects and initialise support tools.
        total_lines = self._estimate_project_lines()
        if total_lines > _LARGE_PROJECT_THRESHOLD:
            self._large_project = True
            self._init_large_project_tools()
            yield f"Large project detected ({total_lines:,} lines). Using project map."
        else:
            self._large_project = False

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

        # Clear the notepad for the next task.
        if self._notepad is not None:
            self._notepad.clear()

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

        system = self._build_system_prompt()

        messages = [
            ChatMessage(role="system", content=system),
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

        # Confirm overwrite if the file already exists.
        full_path = self._workspace / path
        if full_path.exists() and self._confirm is not None:
            approved = await self._confirm.confirm(f"overwrite {path}")
            if not approved:
                return f"Skipped overwriting {path} — not confirmed."

        prompt = (
            f"Create the file `{path}` with the following purpose:\n"
            f"{description}\n\n"
        )

        # For large projects, inject notepad context so the model knows
        # about already-explored files.
        if self._notepad is not None and not self._notepad.is_empty:
            prompt += (
                "Context from previous steps:\n"
                + self._notepad.to_context()
                + "\n\n"
            )

        prompt += (
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

        # Remember what we created in the notepad.
        if self._notepad is not None:
            summary_parts = [b.path for b in blocks]
            self._notepad.remember_file(
                path, f"created — {', '.join(summary_parts)}")

        return "; ".join(results)

    async def _write_file(self, path: str, content: str) -> str:
        """Write content to disk after sandbox validation and syntax check."""
        resolved = self._validate_sandbox(path)

        # Ensure parent directories exist.
        resolved.parent.mkdir(parents=True, exist_ok=True)

        # Language-aware syntax validation.
        ext = resolved.suffix.lower()
        lang_name = get_language_for_extension(ext)
        if lang_name:
            lang_config = get_language_config(lang_name)
            if lang_config.validate_syntax is not None:
                try:
                    result = lang_config.validate_syntax(content, str(resolved))
                    if result is False:
                        return f"Syntax error in {path}: validation failed"
                except SyntaxError as exc:
                    return f"Syntax error in {path}: {exc}"

        await asyncio.to_thread(resolved.write_text, content, encoding="utf-8")
        return f"Created {path} ({len(content)} chars)"

    # -- file editing ------------------------------------------------------

    async def _edit_file(self, path: str, instruction: str) -> str:
        """Read file, ask model to edit it, write back.

        For large projects the notepad context is injected so the model
        knows about previously explored files.
        """
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
        )

        # Inject notepad context for large projects.
        if self._notepad is not None and not self._notepad.is_empty:
            prompt += (
                "Context from previous steps:\n"
                + self._notepad.to_context()
                + "\n\n"
            )

        prompt += (
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

        # Remember what we edited.
        if self._notepad is not None:
            self._notepad.remember_file(path, f"edited — {instruction[:80]}")

        return result

    # -- shell commands ----------------------------------------------------

    async def _run_command(self, command: str) -> tuple[str, str, int]:
        """Run a shell command, return (stdout, stderr, returncode).

        The command runs with ``shell=False`` (split via shlex) inside the
        workspace directory.  A 60-second timeout is enforced.
        """
        # Voice confirmation for arbitrary shell commands (except tests).
        _SAFE_COMMANDS = {
            "python -m pytest -q",
            "python -m pytest",
        }
        if self._confirm is not None and command not in _SAFE_COMMANDS:
            approved = await self._confirm.confirm(f"run command: {command}")
            if not approved:
                return ("", "Skipped — not confirmed.", 1)
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
        """Run tests using the language-appropriate runner.

        For Python the original ``sys.executable -m pytest -q`` is kept.
        Other languages delegate to their ``LanguageConfig.test_command``.
        """
        lang = self._get_language()
        lang_config = get_language_config(lang)

        if lang == "python":
            cmd = [sys.executable, "-m", "pytest", "-q"]
        elif lang_config.test_command:
            import shlex
            cmd = shlex.split(lang_config.test_command)
        else:
            return (True, "No test command configured for this language.")

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
            return (False, f"Test runner not found: {cmd[0]}")
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

        # Voice confirmation for commit and push.
        if action in ("commit", "push") and self._confirm is not None:
            msg = kwargs.get("message", "")
            desc = f"git {action}"
            if msg:
                desc += f" with message '{msg}'"
            approved = await self._confirm.confirm(desc)
            if not approved:
                return f"Skipped git {action} — not confirmed."

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

        lang = self._get_language()
        lang_config = get_language_config(lang)

        # Language-appropriate file list for the prompt.
        if lang == "python":
            file_list = (
                "- __init__.py\n"
                "- main.py (entry point)\n"
                "- requirements.txt\n"
                "- README.md\n"
                "- .gitignore\n"
                "- At least one test file in tests/\n"
            )
        else:
            file_list = (
                f"- Entry point file\n"
                f"- {lang_config.package_file} (dependency / build config)\n"
                f"- README.md\n"
                f"- .gitignore\n"
                f"- At least one test file\n"
            )

        prompt = (
            f"Create a complete {lang_config.display_name} project based on "
            f"this description:\n"
            f"{description}\n\n"
            f"Include at minimum:\n"
            f"{file_list}\n"
            f"For each file, use the format:\n"
            f"```filepath: path/to/file\n"
            f"<complete content>\n"
            f"```"
        )

        messages = [
            ChatMessage(role="system", content=self._get_system_prompt()),
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
