"""Agent management tool — start, cancel, and query the autonomous agent.

Provides voice patterns in EN / RU / UK for cancelling a running agent
task via natural voice commands.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.agents.autonomous import AutonomousAgent
from jarvis.tools.registry import ToolResult, VoicePattern

logger = logging.getLogger(__name__)


class AgentToolArgs(BaseModel):
    action: Literal["cancel", "status"] = Field(
        description=(
            "cancel — stop the currently running autonomous agent task; "
            "status — show current agent progress."
        ),
    )


class AgentTool:
    """Tool for managing the autonomous agent via voice or chat."""

    name: str = "agent_control"
    description: str = (
        "Control the autonomous agent: cancel a running task or check status. "
        "(RU: остановить агента, статус агента / "
        "UK: зупинити агента, статус агента)"
    )
    args_schema = AgentToolArgs
    requires_confirmation: bool = False

    # -- Voice patterns: EN / RU / UK for cancel / stop --------------------
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # ── EN ───────────────────────────────────────────────────────────
        VoicePattern(
            regex=r"^(?:stop|cancel|abort)\s+(?:the\s+)?(?:agent|task)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^(?:stop|cancel|abort)\s+(?:the\s+)?(?:autonomous\s+)?(?:agent|task|execution)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^(?:agent|task)\s+(?:status|progress)$",
            priority=150,
            args=lambda m: {"action": "status"},
        ),
        # ── RU ───────────────────────────────────────────────────────────
        VoicePattern(
            regex=r"^(?:стоп|отмени|прекрати)\s+(?:агент[аеу]?|задачу?)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^останови\s+(?:агент[аеу]?|задачу?)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^(?:статус|прогресс)\s+(?:агент[аеу]?|задачи?)$",
            priority=150,
            args=lambda m: {"action": "status"},
        ),
        # ── UK ───────────────────────────────────────────────────────────
        VoicePattern(
            regex=r"^(?:стоп|скасуй|припини)\s+(?:агент[аеу]?|завдання)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^зупини\s+(?:агент[аеу]?|завдання)$",
            priority=100,
            args=lambda m: {"action": "cancel"},
        ),
        VoicePattern(
            regex=r"^(?:статус|прогрес)\s+(?:агент[аеу]?|завдання)$",
            priority=150,
            args=lambda m: {"action": "status"},
        ),
    )

    def __init__(self, *, agent: AutonomousAgent | None = None) -> None:
        self._agent = agent

    def set_agent(self, agent: AutonomousAgent) -> None:
        """Bind the active agent instance (called when an agent starts)."""
        self._agent = agent

    async def execute(self, args: AgentToolArgs) -> ToolResult:
        if self._agent is None:
            return ToolResult(success=False, error="No active agent to control.")

        if args.action == "cancel":
            self._agent.cancel()
            return ToolResult(success=True, output="Agent cancel signal sent.")

        if args.action == "status":
            summary = self._agent._build_summary(partial=True)
            return ToolResult(success=True, output=summary)

        return ToolResult(success=False, error=f"Unknown action: {args.action}")
