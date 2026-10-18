"""Autonomous Agent voice tool — catch-all for complex multi-step goals.

Bridges the ``AutonomousAgent`` (jarvis/agents/autonomous.py) to the
voice / tool layer.  Accepts free-form goals and delegates to the agent,
which plans, selects tools, and executes in a loop.

Voice examples (EN / RU / UK):

    "Do organise my Downloads into folders"
    "Handle setting up my new project"
    "Figure out why my internet is slow"
    "Сделай резервную копию моих документов"
    "Разберись с файлами на рабочем столе"
    "Зроби порядок у завантаженнях"
    "Виконай очистку тимчасових файлів"

Priority 500 = low priority — only matches when no specific tool pattern
caught the phrase.  "Do X" / "Сделай X" is the catch-all for complex goals.

Security:
    The tool itself is WRITE-level (creates / edits / moves files via
    sub-tools).  Destructive actions within the agent are further gated
    by ``AgentConfig.confirm_destructive`` — the agent will ask for
    confirmation before executing destructive tools.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class AutonomousAgentArgs(BaseModel):
    goal: str = Field(
        description=(
            "The goal to accomplish.  Free-form natural language: "
            "organise files, clean up, set up a project, etc."
        ),
    )


# ---------------------------------------------------------------------------
# Panel helper tools
# ---------------------------------------------------------------------------

class OpenAgentPanelTool:
    """Open the autonomous agent panel."""

    name: str = "open_agent_panel"
    description: str = "Open the autonomous agent panel."
    args_schema = type("_Empty", (BaseModel,), {})
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:open|show)\s+agent\s+panel$",
            priority=100,
        ),
        VoicePattern(
            regex=r"^(?:открой|покажи)\s+панель\s+агента$",
            priority=100,
        ),
        VoicePattern(
            regex=r"^(?:відкрий|покажи)\s+панель\s+агента$",
            priority=100,
        ),
    )

    def __init__(self, *, on_open: Any | None = None) -> None:
        self._on_open = on_open

    async def execute(self, args: Any) -> ToolResult:
        if self._on_open is not None:
            self._on_open()
        return ToolResult(success=True, output="Agent panel opened.")


class CloseAgentPanelTool:
    """Close the autonomous agent panel."""

    name: str = "close_agent_panel"
    description: str = "Close the autonomous agent panel."
    args_schema = type("_Empty", (BaseModel,), {})
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^close\s+agent\s+panel$",
            priority=100,
        ),
        VoicePattern(
            regex=r"^закрой\s+панель\s+агента$",
            priority=100,
        ),
        VoicePattern(
            regex=r"^закрий\s+панель\s+агента$",
            priority=100,
        ),
    )

    def __init__(self, *, on_close: Any | None = None) -> None:
        self._on_close = on_close

    async def execute(self, args: Any) -> ToolResult:
        if self._on_close is not None:
            self._on_close()
        return ToolResult(success=True, output="Agent panel closed.")


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

class AgentTool:
    """Autonomous agent — plans and executes multi-step goals using all
    available tools.

    (RU: автономный агент — сделай X / UK: автономний агент — зроби X)
    """

    name: str = "autonomous_agent"
    description: str = (
        "Autonomous agent for complex multi-step tasks.  Give it any "
        "goal in natural language: organise files, clean up, set up a "
        "project, research and summarise, etc.  The agent plans, selects "
        "tools, and executes step by step. "
        "(RU: автономный агент для сложных задач / "
        "UK: автономний агент для складних завдань)"
    )
    args_schema = AutonomousAgentArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # -- EN: general "do X", "handle X", "figure out X" ---------------
        VoicePattern(
            regex=r"^(?:do|accomplish|complete|handle)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:figure out|work on)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
        # -- RU: "сделай X", "выполни X", "разберись X" -------------------
        VoicePattern(
            regex=r"^(?:сделай|выполни|разберись с?)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:займись|обработай)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
        # -- UK: "зроби X", "виконай X", "розберися X" --------------------
        VoicePattern(
            regex=r"^(?:зроби|виконай|розберися з?)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:займися|обробай)\s+(.+)$",
            priority=500,
            args=lambda m: {"goal": m.group(1)},
        ),
    )

    def __init__(
        self,
        *,
        agent_config: Any | None = None,
        registry: Any | None = None,
        ollama_client: Any | None = None,
        agent_panel: Any | None = None,
        speak_fn: Any | None = None,
    ) -> None:
        self._agent_config = agent_config
        self._registry = registry
        self._ollama_client = ollama_client
        self._agent_panel = agent_panel
        self._speak_fn = speak_fn
        self._current_agent: Any | None = None

    async def execute(self, args: AutonomousAgentArgs) -> ToolResult:
        """Run an autonomous agent task."""
        if self._agent_config is None:
            return ToolResult(
                success=False,
                error="Autonomous agent is not configured.",
            )

        if not self._agent_config.enabled:
            return ToolResult(
                success=False,
                error="Autonomous agent is disabled in settings.",
            )

        if self._registry is None:
            return ToolResult(
                success=False,
                error="No tool registry available for the agent.",
            )

        # Build the model provider
        try:
            provider = self._build_provider()
        except Exception as exc:
            log.exception("Failed to build model provider for agent")
            return ToolResult(
                success=False,
                error=f"Failed to initialize model: {exc}",
            )

        from jarvis.agents.autonomous import AutonomousAgent

        confirm_fn = None
        if self._agent_panel is not None:
            confirm_fn = getattr(self._agent_panel, "ask_confirmation", None)

        agent = AutonomousAgent(
            provider=provider,
            registry=self._registry,
            config=self._agent_config,
            confirm_fn=confirm_fn,
        )
        self._current_agent = agent

        # Show panel if available
        if self._agent_panel is not None:
            try:
                self._agent_panel.start_task(args.goal)
            except Exception:
                log.debug("Could not show agent panel", exc_info=True)

        # Run the agent
        messages: list[str] = []
        try:
            async for msg in agent.run(args.goal):
                messages.append(msg)
                # Update panel
                if self._agent_panel is not None:
                    try:
                        self._agent_panel.add_step(msg)
                    except Exception:
                        pass
                # Speak progress
                if self._speak_fn is not None:
                    try:
                        self._speak_fn(msg)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            messages.append("Task cancelled.")
        except Exception as exc:
            log.exception("Agent task failed")
            messages.append(f"Error: {exc}")

        self._current_agent = None

        # Complete panel
        if self._agent_panel is not None:
            try:
                self._agent_panel.finish_task(
                    success=not any("ошибка" in m.lower() or "error" in m.lower()
                                    for m in messages),
                    log=agent.reasoning_log,
                )
            except Exception:
                pass

        summary = messages[-1] if messages else "Task completed."
        return ToolResult(success=True, output=summary)

    def cancel(self) -> None:
        """Cancel the currently running agent."""
        if self._current_agent is not None:
            self._current_agent.cancel()

    def _build_provider(self) -> Any:
        """Build a ModelProvider from the agent config."""
        from jarvis.llm.model_provider import ModelProvider

        provider_name = self._agent_config.model_provider

        if provider_name == "main":
            if self._ollama_client is None:
                raise RuntimeError(
                    "agent model_provider='main' requires an OllamaClient"
                )
            from jarvis.llm.model_provider import OllamaProvider
            return OllamaProvider(client=self._ollama_client, model=None)

        if provider_name == "coding":
            # Use the coding model configuration
            if self._ollama_client is None:
                raise RuntimeError(
                    "agent model_provider='coding' requires an OllamaClient"
                )
            from jarvis.llm.model_provider import OllamaProvider
            return OllamaProvider(client=self._ollama_client, model=None)

        # Default: use main
        if self._ollama_client is None:
            raise RuntimeError("No OllamaClient available for agent")
        from jarvis.llm.model_provider import OllamaProvider
        return OllamaProvider(client=self._ollama_client, model=None)
