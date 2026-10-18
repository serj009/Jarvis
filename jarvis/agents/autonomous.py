"""AutonomousAgent — general-purpose plan-and-execute agent.

Uses the full tool registry (126+ tools) to accomplish arbitrary goals.
Runs in a loop: plan → act → observe → decide.

The agent yields progress strings so the voice / UI layer can relay them
to the user in real time.  Destructive actions are gated by the
``confirm_destructive`` config flag; when True the agent asks the user
before executing any tool whose risk level is DESTRUCTIVE or SYSTEM.

Architecture::

    User: "Organise my Downloads — sort files into folders"
             ↓
    AgentTool (voice pattern match)
             ↓
    AutonomousAgent.run(goal):
      1. Plan: LLM breaks goal into steps
      2. Loop:
         for step in plan:
           action = LLM decides which tool to use
           if action.is_destructive → ask confirmation
           result = execute_tool(action)
           observe(result)
           LLM decides: continue / adjust plan / stop
      3. Done → report results

Security:
    * Hard limit on iterations (``max_iterations``, default 20).
    * Hard timeout (``max_time_seconds``, default 300 s).
    * Destructive actions require explicit user confirmation.
    * All tool calls go through ``ToolRegistry.execute()`` which enforces
      sandbox, risk levels, and per-tool confirmation gates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.core.config import AgentConfig
    from jarvis.llm.model_provider import ChatMessage, ModelProvider
    from jarvis.tools.registry import ToolRegistry

from jarvis.tools.registry import ToolResult
from jarvis.tools.risk import RiskLevel, risk_level_for_tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AgentAction:
    """A single action the agent decided to take."""

    type: str  # "tool_call", "done", "adjust_plan"
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    reasoning: str = ""  # LLM's explanation of WHY
    description: str = ""  # human-readable action description
    is_destructive: bool = False
    new_plan: list[str] = field(default_factory=list)  # for adjust_plan


@dataclass
class AgentStep:
    """One iteration of the agent loop — action + result + reasoning."""

    iteration: int
    action: AgentAction
    result: ToolResult | None
    reasoning: str
    timestamp: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
You are an autonomous AI agent. You have access to tools to accomplish \
the user's goal.  You work step-by-step: decide the next action, observe \
the result, and continue until the goal is achieved.

Available tools:
{tool_descriptions}

History of actions and results:
{action_history}

Current plan:
{plan}

User's goal: {goal}

Decide the NEXT action. Reply in **strict JSON only** (no markdown, no \
explanation outside the JSON):

For a tool call:
{{"type": "tool_call", "tool": "tool_name", "args": {{}}, \
"reasoning": "why this step", "description": "human summary"}}

When the goal is achieved:
{{"type": "done", "reasoning": "goal achieved because...", \
"description": "summary of what was accomplished"}}

To adjust the plan:
{{"type": "adjust_plan", "new_plan": ["step1", "step2", ...], \
"reasoning": "plan changed because..."}}
"""


_PLAN_PROMPT = """\
Break the following goal into concrete steps (2-10 steps).  Each step \
should be one tool call.  Reply in **strict JSON only** — a list of \
short strings, each describing one step.

Available tool names: {tool_names}

Goal: {goal}

Reply: ["step 1 description", "step 2 description", ...]
"""


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _extract_json(text: str) -> dict | list:
    """Extract a JSON object or array from LLM output.

    Tries the raw text first; if that fails, looks for a fenced code
    block.  Raises ``ValueError`` on complete failure.
    """
    text = text.strip()
    # Try raw
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        idx_start = text.find(start_char)
        idx_end = text.rfind(end_char)
        if idx_start != -1 and idx_end > idx_start:
            candidate = text[idx_start : idx_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # Try fenced block
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM output: {text[:200]}")


# ---------------------------------------------------------------------------
# AutonomousAgent
# ---------------------------------------------------------------------------

class AutonomousAgent:
    """General-purpose autonomous agent that plans and executes tasks.

    Uses the full tool registry to accomplish goals.
    Runs in a loop: plan → act → observe → decide.

    Parameters
    ----------
    provider : ModelProvider
        The LLM backend (Ollama, OpenAI, Anthropic, Groq).
    registry : ToolRegistry
        The tool registry with all available tools.
    config : AgentConfig
        Agent configuration (max iterations, timeout, etc.).
    confirm_fn : callable, optional
        Async function ``(AgentAction) -> bool`` that asks the user to
        confirm a destructive action.  When *None* destructive actions
        are refused outright (fail-closed).
    """

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        config: AgentConfig,
        confirm_fn: Callable[[AgentAction], Any] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._config = config
        self._confirm = confirm_fn
        self._reasoning_log: list[AgentStep] = []
        self._plan: list[str] = []
        self._cancelled = False

    # -- public API -------------------------------------------------------

    async def run(self, goal: str) -> AsyncIterator[str]:
        """Execute a goal.  Yields progress messages for TTS / UI.

        The entire run is bounded by ``max_time_seconds``.
        """
        self._reasoning_log.clear()
        self._plan = []
        self._cancelled = False
        start = time.monotonic()

        # 1. Plan
        yield "Планирую, sir..."
        try:
            self._plan = await asyncio.wait_for(
                self._create_plan(goal),
                timeout=min(60, self._config.max_time_seconds),
            )
        except asyncio.TimeoutError:
            yield "Не удалось создать план — таймаут, sir."
            return
        except Exception as exc:
            log.exception("Plan creation failed")
            yield f"Ошибка планирования: {exc}"
            return

        if not self._plan:
            yield "Не удалось разбить задачу на шаги, sir."
            return

        yield f"План готов: {len(self._plan)} шагов"

        if self._config.verbose_reasoning:
            for i, step in enumerate(self._plan, 1):
                yield f"  {i}. {step}"

        # 2. Execute loop
        iterations = 0
        while iterations < self._config.max_iterations:
            if self._cancelled:
                yield "Задача отменена, sir."
                break

            elapsed = time.monotonic() - start
            if elapsed >= self._config.max_time_seconds:
                yield "Достигнут лимит времени, sir."
                break

            remaining = self._config.max_time_seconds - elapsed

            # Decide next action
            try:
                action = await asyncio.wait_for(
                    self._decide_next_action(goal),
                    timeout=min(60, remaining),
                )
            except asyncio.TimeoutError:
                yield "Таймаут при принятии решения, sir."
                break
            except Exception as exc:
                log.exception("Decision failed at iteration %d", iterations)
                yield f"Ошибка: {exc}"
                break

            # Handle action types
            if action.type == "done":
                summary = action.description or action.reasoning
                yield f"Задача выполнена: {summary}"
                self._reasoning_log.append(AgentStep(
                    iteration=iterations,
                    action=action,
                    result=None,
                    reasoning=action.reasoning,
                ))
                break

            if action.type == "adjust_plan":
                self._plan = action.new_plan
                yield f"План скорректирован: {len(self._plan)} шагов"
                self._reasoning_log.append(AgentStep(
                    iteration=iterations,
                    action=action,
                    result=None,
                    reasoning=action.reasoning,
                ))
                iterations += 1
                continue

            if action.type == "tool_call":
                # Check if destructive and needs confirmation
                if action.is_destructive and self._config.confirm_destructive:
                    confirmed = await self._ask_confirmation(action)
                    if not confirmed:
                        yield f"Пропускаю: {action.description}"
                        self._reasoning_log.append(AgentStep(
                            iteration=iterations,
                            action=action,
                            result=ToolResult(
                                success=False,
                                error="User declined confirmation",
                            ),
                            reasoning=action.reasoning,
                        ))
                        iterations += 1
                        continue

                # Execute tool
                desc = action.description or action.tool_name
                yield f"Выполняю: {desc}..."

                try:
                    result = await asyncio.wait_for(
                        self._execute_tool(action),
                        timeout=min(120, remaining),
                    )
                except asyncio.TimeoutError:
                    result = ToolResult(
                        success=False, error="Tool execution timed out"
                    )
                except Exception as exc:
                    log.exception(
                        "Tool %r crashed at iteration %d",
                        action.tool_name, iterations,
                    )
                    result = ToolResult(
                        success=False, error=f"Tool crashed: {exc}"
                    )

                self._reasoning_log.append(AgentStep(
                    iteration=iterations,
                    action=action,
                    result=result,
                    reasoning=action.reasoning,
                ))

                summary = (
                    result.output if result.success
                    else result.error or "unknown error"
                )
                # Truncate long output for TTS
                if isinstance(summary, str) and len(summary) > 200:
                    summary = summary[:200] + "…"
                elif isinstance(summary, dict):
                    summary = str(summary)[:200]

                if result.success:
                    yield f"✓ {summary}"
                else:
                    yield f"✗ {summary}"

            iterations += 1

        if iterations >= self._config.max_iterations:
            yield "Достигнут лимит итераций, sir."

    def cancel(self) -> None:
        """Request cancellation of the running agent."""
        self._cancelled = True

    @property
    def reasoning_log(self) -> list[AgentStep]:
        """Full reasoning log for inspection."""
        return list(self._reasoning_log)

    @property
    def plan(self) -> list[str]:
        """Current plan steps."""
        return list(self._plan)

    # -- internal ---------------------------------------------------------

    async def _create_plan(self, goal: str) -> list[str]:
        """Ask LLM to break goal into steps."""
        from jarvis.llm.model_provider import ChatMessage

        tool_names = [t.name for t in self._registry.list_enabled()]

        prompt = _PLAN_PROMPT.format(
            tool_names=", ".join(tool_names[:50]),  # cap for context
            goal=goal,
        )

        messages = [ChatMessage(role="user", content=prompt)]
        response = await self._provider.chat(
            messages, temperature=0.3, max_tokens=1024,
        )

        parsed = _extract_json(response.content)
        if isinstance(parsed, list):
            return [str(s) for s in parsed[:20]]

        log.warning("Plan response was not a list: %s", type(parsed))
        return []

    async def _decide_next_action(self, goal: str) -> AgentAction:
        """Ask LLM: given history + plan, what's the next action?"""
        from jarvis.llm.model_provider import ChatMessage

        # Build tool descriptions (filtered for relevance — top 30)
        enabled = self._registry.list_enabled()
        tool_descs = "\n".join(
            f"- {t.name}: {t.description}"
            for t in enabled[:30]
        )

        # Build action history
        history_lines: list[str] = []
        for step in self._reasoning_log[-10:]:  # last 10 steps
            act = step.action
            if act.type == "tool_call":
                status = "OK" if step.result and step.result.success else "FAILED"
                out = ""
                if step.result:
                    out = str(step.result.output or step.result.error or "")
                    if len(out) > 300:
                        out = out[:300] + "…"
                history_lines.append(
                    f"[{act.tool_name}] {status}: {out}"
                )
            elif act.type == "adjust_plan":
                history_lines.append(f"[plan adjusted]: {act.reasoning}")
            elif act.type == "done":
                history_lines.append(f"[done]: {act.reasoning}")

        action_history = "\n".join(history_lines) or "(no actions yet)"
        plan_text = "\n".join(
            f"  {i + 1}. {s}" for i, s in enumerate(self._plan)
        ) or "(no plan)"

        prompt = AGENT_SYSTEM_PROMPT.format(
            tool_descriptions=tool_descs,
            action_history=action_history,
            plan=plan_text,
            goal=goal,
        )

        messages = [ChatMessage(role="user", content=prompt)]
        response = await self._provider.chat(
            messages, temperature=0.2, max_tokens=1024,
        )

        return self._parse_action(response.content)

    def _parse_action(self, text: str) -> AgentAction:
        """Parse the LLM's JSON response into an AgentAction."""
        try:
            data = _extract_json(text)
        except ValueError:
            log.warning("Could not parse action JSON: %s", text[:300])
            return AgentAction(
                type="done",
                reasoning="Could not parse action response",
                description="Agent stopped due to parse error",
            )

        if not isinstance(data, dict):
            return AgentAction(
                type="done",
                reasoning="Action response was not a JSON object",
            )

        action_type = data.get("type", "done")
        if action_type not in ("tool_call", "done", "adjust_plan"):
            action_type = "done"

        tool_name = str(data.get("tool", ""))
        tool_args = data.get("args", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        reasoning = str(data.get("reasoning", ""))
        description = str(data.get("description", ""))
        new_plan = data.get("new_plan", [])
        if not isinstance(new_plan, list):
            new_plan = []
        new_plan = [str(s) for s in new_plan]

        # Determine if the tool is destructive
        is_destructive = False
        if action_type == "tool_call" and tool_name:
            tool = self._registry.get(tool_name)
            if tool is not None:
                level = risk_level_for_tool(tool)
                is_destructive = level >= RiskLevel.DESTRUCTIVE

        return AgentAction(
            type=action_type,
            tool_name=tool_name,
            tool_args=tool_args,
            reasoning=reasoning,
            description=description,
            is_destructive=is_destructive,
            new_plan=new_plan,
        )

    async def _execute_tool(self, action: AgentAction) -> ToolResult:
        """Execute a tool from the registry."""
        return await self._registry.execute(action.tool_name, action.tool_args)

    async def _ask_confirmation(self, action: AgentAction) -> bool:
        """Ask user to confirm destructive action.

        Returns True if confirmed, False otherwise.
        Fails closed: no confirm_fn → refused.
        """
        if self._confirm is None:
            log.warning(
                "Destructive action %r requires confirmation but no "
                "confirm_fn is wired; refusing.",
                action.tool_name,
            )
            return False

        try:
            result = self._confirm(action)
            if asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("confirm_fn raised for %r; treating as denied",
                          action.tool_name)
            return False
