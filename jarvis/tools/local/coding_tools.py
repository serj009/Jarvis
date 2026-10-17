"""Coding Agent voice tool — full coding assistant via voice commands (T9.5).

Bridges the ``CodingAgent`` (jarvis/coding/agent.py) to the voice / tool
layer.  Accepts free-form coding tasks and delegates to the agent, which
plans, executes, and yields progress messages.

Voice examples (EN / RU / UK):

    "Create a project for web scraping"
    "Add logging to the current project"
    "Write a REST API with Flask"
    "Fix the failing tests"
    "Commit and push to GitHub"
    "Создай проект для парсинга новостей"
    "Напиши REST API на Flask"
    "Исправь ошибки в тестах"
    "Створи проект для веб-скрапінгу"

Security:
    The tool itself is WRITE-level (creates / edits files, runs tests).
    Destructive git operations (push) are further gated by the CodingConfig
    ``auto_commit`` flag — when False the agent will not push without an
    explicit "push" voice command.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class CodingAgentArgs(BaseModel):
    task: str = Field(
        description=(
            "The coding task to execute.  Free-form natural language: "
            "create a project, write code, fix bugs, run tests, git operations."
        ),
    )


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------

class CodingAgentTool:
    """Full coding assistant: create projects, write code, run tests,
    fix bugs, git operations.

    (RU: создай проект, напиши код, исправь ошибки, запусти тесты /
     UK: створи проект, напиши код, виправ помилки, запусти тести)
    """

    name: str = "coding_agent"
    description: str = (
        "Full coding assistant: create projects, write code, run tests, "
        "fix bugs, git.  Give it any coding task in natural language. "
        "(RU: полный ассистент кодирования / UK: повний асистент кодування)"
    )
    args_schema = CodingAgentArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # -- EN: project creation ------------------------------------------
        VoicePattern(
            regex=r"^(?:create|make|start)\s+(?:a\s+)?(?:new\s+)?project\s+(?:for\s+|about\s+)?(.+)$",
            priority=150,
            args=lambda m: {"task": f"create a project for {m.group(1)}"},
        ),
        # -- EN: code writing (require coding-related noun to avoid stealing
        #    from type_into_active_window "write X" at priority 800) --------
        VoicePattern(
            regex=(
                r"^(?:write|add|implement|code)\s+"
                r"(?:a\s+|the\s+)?"
                r"((?:function|class|module|test|endpoint|api|script|"
                r"method|handler|route|service|model|schema|decorator|"
                r"middleware|parser|validator|cli|command|tool|utility).*)$"
            ),
            priority=300,
            args=lambda m: {"task": m.group(1)},
        ),
        # -- EN: fix / debug -----------------------------------------------
        VoicePattern(
            regex=r"^(?:fix|debug|repair)\s+(?:the\s+)?(?:errors?|bugs?|tests?|code)$",
            priority=200,
            args=lambda m: {"task": "fix the failing tests and errors"},
        ),
        # -- RU: project creation ------------------------------------------
        VoicePattern(
            regex=r"^создай\s+проект\s+(?:для\s+|про\s+)?(.+)$",
            priority=150,
            args=lambda m: {"task": f"создай проект для {m.group(1)}"},
        ),
        # -- RU: code writing (require coding noun) -------------------------
        VoicePattern(
            regex=(
                r"^(?:напиши|добавь|реализуй)\s+"
                r"(?:(?:функцию|класс|модуль|тест|эндпоинт|апи|скрипт|"
                r"метод|обработчик|роут|сервис|модель|схему|команду|утилиту)"
                r"\s+.+|.+\s+(?:на\s+(?:python|flask|django|fastapi)))$"
            ),
            priority=280,
            args=lambda m: {"task": m.group(1)},
        ),
        # -- RU: fix / debug -----------------------------------------------
        VoicePattern(
            regex=r"^(?:почини|исправь|отладь)\s+(?:ошибки?|баги?|тесты?)$",
            priority=200,
            args=lambda m: {"task": "исправь ошибки в тестах"},
        ),
        # -- UK: project creation ------------------------------------------
        VoicePattern(
            regex=r"^створи\s+проект\s+(?:для\s+|про\s+)?(.+)$",
            priority=150,
            args=lambda m: {"task": f"створи проект для {m.group(1)}"},
        ),
        # -- UK: code writing (require coding noun) -------------------------
        VoicePattern(
            regex=(
                r"^(?:напиши|додай|реалізуй)\s+"
                r"(?:(?:функцію|клас|модуль|тест|ендпоінт|апі|скрипт|"
                r"метод|обробник|роут|сервіс|модель|схему|команду|утиліту)"
                r"\s+.+|.+\s+(?:на\s+(?:python|flask|django|fastapi)))$"
            ),
            priority=280,
            args=lambda m: {"task": m.group(1)},
        ),
        # -- UK: fix / debug -----------------------------------------------
        VoicePattern(
            regex=r"^(?:полагодь|виправ|відлагодь)\s+(?:помилки?|баги?|тести?)$",
            priority=200,
            args=lambda m: {"task": "виправ помилки в тестах"},
        ),
    )

    def __init__(
        self,
        *,
        coding_config: Any | None = None,
        provider: Any | None = None,
        ollama_client: Any | None = None,
        workspace: str | Path | None = None,
        speak_fn: Any | None = None,
        listen_fn: Any | None = None,
        coding_panel: Any | None = None,
    ) -> None:
        self._coding_config = coding_config
        self._provider = provider
        self._ollama_client = ollama_client
        self._workspace = Path(workspace) if workspace else None
        self._speak_fn = speak_fn
        self._listen_fn = listen_fn
        self._coding_panel = coding_panel

    async def execute(self, args: CodingAgentArgs) -> ToolResult:
        """Build a CodingAgent and run the task, collecting all progress."""
        from jarvis.coding.agent import CodingAgent
        from jarvis.coding.voice_confirm import VoiceConfirm

        config = self._coding_config
        if config is None:
            from jarvis.core.config import CodingConfig
            config = CodingConfig()

        provider = self._provider
        if provider is None:
            # Lazily build provider from config + ollama_client.
            try:
                from jarvis.llm.model_provider import ModelProvider
                provider = ModelProvider.from_config(
                    config, self._ollama_client,
                )
            except Exception:
                pass

        if provider is None:
            if self._coding_panel is not None:
                from jarvis.coding.task_storage import CodingTask
                fail = CodingTask(name=args.task, status="failed")
                self._coding_panel.sig_task_updated.emit(fail)
            return ToolResult(
                success=False,
                error=(
                    "Coding agent requires a model provider. "
                    "Configure coding settings in Settings → Coding."
                ),
            )

        workspace = self._workspace
        if workspace is None:
            default_ws = getattr(config, "default_workspace", "")
            if default_ws:
                workspace = Path(default_ws)
            else:
                workspace = Path.cwd()

        # -- Panel: open and create a CodingTask --------------------------
        panel = self._coding_panel
        from jarvis.coding.task_storage import CodingTask, TaskStep
        task_record = CodingTask(name=args.task, workspace_path=str(workspace))

        if panel is not None:
            panel.sig_open.emit()
            panel.sig_task_updated.emit(task_record)

        # -- VoiceConfirm --------------------------------------------------
        # Build VoiceConfirm if TTS/STT callbacks are available.
        confirm: VoiceConfirm | None = None
        if self._speak_fn is not None and self._listen_fn is not None:
            confirm = VoiceConfirm(
                speak_fn=self._speak_fn,
                listen_fn=self._listen_fn,
            )

        agent = CodingAgent(
            provider=provider,
            workspace=workspace,
            config=config,
            confirm=confirm,
        )

        messages: list[str] = []
        try:
            step_idx = 0
            async for msg in agent.execute_task(args.task):
                messages.append(msg)

                # -- Push progress to panel --------------------------------
                if panel is not None:
                    step = TaskStep(
                        title=msg,
                        status="done",
                    )
                    # Append as a new completed step.
                    task_record.steps.append(step)
                    step_idx += 1
                    panel.sig_task_updated.emit(task_record)

        except Exception as exc:
            log.exception("coding agent failed for task %r", args.task)

            if panel is not None:
                task_record.status = "failed"
                task_record.steps.append(TaskStep(
                    title=f"Error: {exc}",
                    status="error",
                ))
                panel.sig_task_updated.emit(task_record)
                panel.sig_notification.emit(
                    "Coding Task Failed", f"{args.task}: {exc}",
                )

            return ToolResult(
                success=False,
                error=f"Coding agent error: {exc}",
            )

        output = "\n".join(messages) if messages else "No output from coding agent."
        # Determine success from the last message.
        success = bool(messages) and "error" not in messages[-1].lower()

        # -- Finalise task record -----------------------------------------
        if panel is not None:
            task_record.status = "done" if success else "failed"
            panel.sig_task_updated.emit(task_record)
            panel.sig_notification.emit(
                "Coding Task Complete" if success else "Coding Task Failed",
                args.task,
            )

        return ToolResult(success=success, output=output)


# ---------------------------------------------------------------------------
# Panel open / close tools
# ---------------------------------------------------------------------------


class OpenCodingPanelArgs(BaseModel):
    pass


class OpenCodingPanelTool:
    """Open the Coding Agent panel.

    (RU: открой панель кода / UK: відкрий панель коду)
    """

    name: str = "open_coding_panel"
    description: str = (
        "Open the Coding Agent panel."
    )
    args_schema = OpenCodingPanelArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:open|show)\s+(?:the\s+)?coding\s+panel$",
            priority=150,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:открой|покажи)\s+панель\s+кода$",
            priority=150,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:відкрий|покажи)\s+панель\s+коду$",
            priority=150,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, on_open: Any = None) -> None:
        self._on_open = on_open

    async def execute(self, args: OpenCodingPanelArgs) -> ToolResult:
        if self._on_open is not None:
            self._on_open()
        return ToolResult(success=True, output="Coding panel opened.")


class CloseCodingPanelArgs(BaseModel):
    pass


class CloseCodingPanelTool:
    """Close the Coding Agent panel.

    (RU: закрой панель кода / UK: закрий панель коду)
    """

    name: str = "close_coding_panel"
    description: str = (
        "Close the Coding Agent panel."
    )
    args_schema = CloseCodingPanelArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:close|hide)\s+(?:the\s+)?coding\s+panel$",
            priority=150,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:закрой|скрой)\s+панель\s+кода$",
            priority=150,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:закрий|сховай)\s+панель\s+коду$",
            priority=150,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, on_close: Any = None) -> None:
        self._on_close = on_close

    async def execute(self, args: CloseCodingPanelArgs) -> ToolResult:
        if self._on_close is not None:
            self._on_close()
        return ToolResult(success=True, output="Coding panel closed.")
