"""AutonomousAgent — general-purpose plan-and-execute agent.

Uses the full tool registry (126+ tools) to accomplish arbitrary goals.
Runs in a loop: plan → act → observe → decide.

The agent yields progress strings so the voice / UI layer can relay them
to the user in real time.  Destructive actions are gated by the
``confirm_destructive`` config flag; when True the agent asks the user
before executing any tool whose risk level is DESTRUCTIVE or SYSTEM.

Built-in mechanisms
-------------------
1. **Scratchpad** — :class:`AgentScratchpad` stores intermediate results
   across steps and is injected into LLM context.
2. **Sliding Window** — only the last *N* steps are shown in full detail;
   older steps are compressed to one-line summaries.
3. **Periodic Re-planning** — every *REPLAN_INTERVAL* steps the agent
   asks the LLM for a fresh plan.
4. **Sub-agent Delegation** — the LLM can emit ``sub_task`` to spawn a
   child :class:`AutonomousAgent` with clean context.
5. **Partial Results** — on cancel/timeout the agent summarises what was
   accomplished so far.
6. **Error Recovery** — on tool failure the LLM picks retry / skip / abort
   (up to ``MAX_RETRIES`` per step).
7. **Human-in-the-Loop** — the LLM can emit ``ask_user`` to query the
   user mid-run.

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
import copy
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.core.config import AgentConfig
    from jarvis.llm.model_provider import ChatMessage, ModelProvider
    from jarvis.tools.registry import ToolRegistry

from jarvis.agents.scratchpad import AgentScratchpad
from jarvis.tools.registry import ToolResult
from jarvis.tools.risk import RiskLevel, risk_level_for_tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DETAILED_HISTORY_SIZE: int = 3        # sliding window: last N steps in detail
REPLAN_INTERVAL: int = 5              # re-plan every N iterations
MAX_RETRIES: int = 3                  # max retries per failed step


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AgentAction:
    """A single action the agent decided to take."""

    type: str  # "tool_call", "done", "adjust_plan", "sub_task", "ask_user"
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    reasoning: str = ""  # LLM's explanation of WHY
    description: str = ""  # human-readable action description
    is_destructive: bool = False
    new_plan: list[str] = field(default_factory=list)  # for adjust_plan
    question: str = ""  # for ask_user
    options: list[str] = field(default_factory=list)  # for ask_user


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

To delegate a complex sub-task to a fresh sub-agent:
{{"type": "sub_task", "description": "detailed sub-goal to delegate"}}

To ask the user a question before continuing:
{{"type": "ask_user", "question": "your question", \
"options": ["option1", "option2"]}}
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
    ask_fn : callable, optional
        Async function ``(str, list[str] | None) -> str`` for human-in-
        the-loop queries.  Falls back to *confirm_fn* when not provided.
    """

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        config: AgentConfig,
        confirm_fn: Callable[[AgentAction], Any] | None = None,
        *,
        ask_fn: Callable[[str, list[str] | None], Awaitable[str]] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._config = config
        self._confirm = confirm_fn
        self._ask_fn = ask_fn
        self._reasoning_log: list[AgentStep] = []
        self._plan: list[str] = []
        self._cancelled = False
        # --- Scratchpad (feature 1) ---
        self._scratchpad = AgentScratchpad()
        # --- Re-planning (feature 3) ---
        self._current_plan: str = ""

    # -- public API -------------------------------------------------------

    async def run(self, goal: str) -> AsyncIterator[str]:
        """Execute a goal.  Yields progress messages for TTS / UI.

        The entire run is bounded by ``max_time_seconds``.
        """
        self._reasoning_log.clear()
        self._plan = []
        self._cancelled = False
        self._scratchpad.clear()
        self._current_plan = ""
        start = time.monotonic()

        # 1. Plan
        yield "Планирую, sir..."
        try:
            self._plan = await asyncio.wait_for(
                self._create_plan(goal),
                timeout=min(60, self._config.max_time_seconds),
            )
        except TimeoutError:
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
                partial = await self._save_partial_results()
                yield "Задача отменена, sir."
                if partial:
                    yield partial
                break

            elapsed = time.monotonic() - start
            if elapsed >= self._config.max_time_seconds:
                partial = await self._save_partial_results()
                yield "Достигнут лимит времени, sir."
                if partial:
                    yield partial
                break

            remaining = self._config.max_time_seconds - elapsed

            # --- Re-planning (feature 3) ---
            if (
                iterations > 0
                and iterations % REPLAN_INTERVAL == 0
                and self._reasoning_log
            ):
                yield "Reviewing plan..."
                try:
                    self._current_plan = await self._replan(goal)
                except Exception as exc:
                    log.warning("Re-planning failed: %s", exc)

            # Decide next action
            try:
                action = await asyncio.wait_for(
                    self._decide_next_action(goal),
                    timeout=min(60, remaining),
                )
            except TimeoutError:
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

            # --- Sub-agent delegation (feature 4) ---
            if action.type == "sub_task":
                yield f"Delegating: {action.description}"
                async for msg in self._run_subtask(action.description):
                    yield msg
                self._reasoning_log.append(AgentStep(
                    iteration=iterations,
                    action=action,
                    result=ToolResult(
                        success=True,
                        output="Sub-task completed",
                    ),
                    reasoning=action.reasoning,
                ))
                iterations += 1
                continue

            # --- Human-in-the-loop (feature 7) ---
            if action.type == "ask_user":
                question = action.question or action.description
                yield f"Question: {question}"
                if action.options:
                    yield f"Options: {', '.join(action.options)}"
                answer = await self._ask_user(
                    question, action.options or None,
                )
                self._scratchpad.set(
                    f"user_answer_{iterations}", answer,
                )
                yield f"Answer: {answer}"
                self._reasoning_log.append(AgentStep(
                    iteration=iterations,
                    action=action,
                    result=ToolResult(
                        success=True,
                        output=f"User answered: {answer}",
                    ),
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

                # Execute tool with error recovery (feature 6)
                desc = action.description or action.tool_name
                yield f"Выполняю: {desc}..."

                retries = 0
                result: ToolResult | None = None
                while True:
                    try:
                        result = await asyncio.wait_for(
                            self._execute_tool(action),
                            timeout=min(120, remaining),
                        )
                    except TimeoutError:
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

                    if result.success:
                        # --- Scratchpad (feature 1) ---
                        output_str = str(result.output or "")
                        self._scratchpad.set(
                            f"step_{iterations}", output_str,
                        )
                        break

                    # --- Error recovery (feature 6) ---
                    if retries < MAX_RETRIES:
                        recovery = await self._handle_step_error(
                            action, result.error or "unknown error",
                        )
                        if recovery == "retry":
                            retries += 1
                            yield f"  Retrying ({retries}/{MAX_RETRIES})..."
                            continue
                        if recovery == "abort":
                            self._reasoning_log.append(AgentStep(
                                iteration=iterations,
                                action=action,
                                result=result,
                                reasoning=action.reasoning,
                            ))
                            partial = await self._save_partial_results()
                            err_msg = result.error or "unknown error"
                            if len(err_msg) > 200:
                                err_msg = err_msg[:200] + "\u2026"
                            yield f"\u2717 {err_msg}"
                            yield f"Aborting.{(' ' + partial) if partial else ''}"
                            self._scratchpad.clear()
                            return
                        # skip
                        break
                    else:
                        # exhausted retries → treat as skip
                        break

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
                    summary = summary[:200] + "\u2026"
                elif isinstance(summary, dict):
                    summary = str(summary)[:200]

                if result.success:
                    yield f"\u2713 {summary}"
                else:
                    yield f"\u2717 {summary}"

            iterations += 1

        if iterations >= self._config.max_iterations:
            partial = await self._save_partial_results()
            yield "Достигнут лимит итераций, sir."
            if partial:
                yield partial

        # --- Scratchpad cleanup (feature 1) ---
        self._scratchpad.clear()

    def cancel(self) -> None:
        """Request cancellation of the running agent."""
        self._cancelled = True

    @property
    def scratchpad(self) -> AgentScratchpad:
        """Read-only access to the scratchpad (working memory)."""
        return self._scratchpad

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

        # --- Sliding window (feature 2) ---
        action_history = self._build_history_context()

        plan_text = "\n".join(
            f"  {i + 1}. {s}" for i, s in enumerate(self._plan)
        ) or "(no plan)"

        # Append re-plan context if available (feature 3)
        if self._current_plan:
            plan_text += f"\n\nUpdated plan:\n{self._current_plan}"

        prompt = AGENT_SYSTEM_PROMPT.format(
            tool_descriptions=tool_descs,
            action_history=action_history,
            plan=plan_text,
            goal=goal,
        )

        # --- Scratchpad injection (feature 1) ---
        scratchpad_ctx = self._scratchpad.to_context()
        if scratchpad_ctx:
            prompt += f"\n\n{scratchpad_ctx}"

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
        if action_type not in (
            "tool_call", "done", "adjust_plan", "sub_task", "ask_user",
        ):
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

        # ask_user fields (feature 7)
        question = str(data.get("question", ""))
        options_raw = data.get("options", [])
        if not isinstance(options_raw, list):
            options_raw = []
        options = [str(o) for o in options_raw]

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
            question=question,
            options=options,
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

    # =====================================================================
    #  Feature 1 — Scratchpad helpers (stored on self._scratchpad)
    # =====================================================================
    # AgentScratchpad instance is created in __init__; results are stored
    # after each successful tool call; context is injected in
    # _decide_next_action; cleared at start and end of run().

    # =====================================================================
    #  Feature 2 — Sliding Window History
    # =====================================================================

    def _build_history_context(self) -> str:
        """Build step history with sliding-window compression.

        Recent steps (last :data:`DETAILED_HISTORY_SIZE`) are shown in full
        detail.  Older steps are collapsed to one-line summaries to keep
        the LLM prompt compact even after 30-50+ steps.
        """
        if not self._reasoning_log:
            return "(no actions yet)"

        if len(self._reasoning_log) <= DETAILED_HISTORY_SIZE:
            return self._format_steps_detailed(self._reasoning_log)

        # Old steps — one-line summary each
        old = self._reasoning_log[:-DETAILED_HISTORY_SIZE]
        summary_lines: list[str] = []
        for step in old:
            act = step.action
            status = "\u2713" if (step.result and step.result.success) else "\u2717"
            desc = act.description or act.tool_name or act.type
            summary_lines.append(f"{status} {desc}")

        # Recent steps — full detail
        recent = self._reasoning_log[-DETAILED_HISTORY_SIZE:]
        detail = self._format_steps_detailed(recent)

        return (
            "Previous steps (summary):\n"
            + "\n".join(summary_lines)
            + "\n\n"
            + f"Recent steps (detail):\n{detail}"
        )

    def _format_steps_detailed(self, steps: list[AgentStep]) -> str:
        """Format step-log entries with full output/error detail."""
        lines: list[str] = []
        for step in steps:
            act = step.action
            if act.type == "tool_call":
                status = "OK" if (step.result and step.result.success) else "FAILED"
                out = ""
                if step.result:
                    out = str(step.result.output or step.result.error or "")
                    if len(out) > 300:
                        out = out[:300] + "\u2026"
                lines.append(f"[{act.tool_name}] {status}: {out}")
            elif act.type == "adjust_plan":
                lines.append(f"[plan adjusted]: {act.reasoning}")
            elif act.type == "sub_task":
                lines.append(f"[sub_task]: {act.description}")
            elif act.type == "ask_user":
                lines.append(f"[ask_user]: {act.question or act.description}")
            elif act.type == "done":
                lines.append(f"[done]: {act.reasoning}")
            else:
                lines.append(f"[{act.type}]: {act.reasoning or act.description}")
        return "\n".join(lines) or "(no actions yet)"

    # =====================================================================
    #  Feature 3 — Periodic Re-planning
    # =====================================================================

    async def _replan(self, goal: str) -> str:
        """Fresh planning with clean context.

        Called every :data:`REPLAN_INTERVAL` iterations to give the LLM a
        clean view of what remains, preventing context drift on small models.

        Returns
        -------
        str
            Updated plan text for the remaining work.
        """
        from jarvis.llm.model_provider import ChatMessage

        # Summarise completed steps
        completed_lines: list[str] = []
        for step in self._reasoning_log:
            status = "\u2713" if (step.result and step.result.success) else "\u2717"
            desc = step.action.description or step.action.tool_name or step.action.type
            completed_lines.append(f"{status} {desc}")
        completed_text = "\n".join(completed_lines)

        scratchpad_ctx = self._scratchpad.to_context()

        prompt = f"Goal: {goal}\n\nCompleted steps:\n{completed_text}\n"
        if scratchpad_ctx:
            prompt += f"\n{scratchpad_ctx}\n"
        prompt += (
            "\nCreate an updated plan for the REMAINING work. "
            "Do NOT repeat completed steps. "
            "Respond with a numbered list of remaining steps."
        )

        response = await self._provider.chat(
            [ChatMessage(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=1024,
        )
        return response.content.strip()

    # =====================================================================
    #  Feature 4 — Sub-agent Delegation
    # =====================================================================

    async def _run_subtask(self, description: str) -> AsyncIterator[str]:
        """Spawn a sub-agent with clean context for a complex sub-task.

        The sub-agent gets halved iteration/timeout budgets and a fresh
        scratchpad so the parent's context stays lean.

        Yields
        ------
        str
            Progress messages from the sub-agent, prefixed with ``[sub]``.
        """
        sub_config = copy.copy(self._config)
        sub_config.max_iterations = max(1, self._config.max_iterations // 2)
        sub_config.max_time_seconds = max(30, self._config.max_time_seconds // 2)

        sub_agent = AutonomousAgent(
            provider=self._provider,
            registry=self._registry,
            config=sub_config,
            confirm_fn=self._confirm,
            ask_fn=self._ask_fn,
        )

        result_parts: list[str] = []
        async for msg in sub_agent.run(description):
            result_parts.append(msg)
            yield f"[sub] {msg}"

        # Store aggregated result in parent scratchpad
        safe_key = f"subtask_{description[:30].replace(' ', '_')}"
        self._scratchpad.set(safe_key, "\n".join(result_parts))

    # =====================================================================
    #  Feature 5 — Partial Results
    # =====================================================================

    async def _save_partial_results(self) -> str:
        """Summarise what was accomplished so far.

        Called on cancel, timeout, or abort so the user knows what
        happened before the agent stopped.

        Returns
        -------
        str
            Human-readable summary (empty string when nothing was done).
        """
        if not self._reasoning_log:
            return ""

        completed = sum(
            1 for s in self._reasoning_log
            if s.result and s.result.success
        )
        total = len(self._reasoning_log)
        lines: list[str] = [f"Completed {completed}/{total} steps:"]
        for step in self._reasoning_log:
            status = "\u2713" if (step.result and step.result.success) else "\u2717"
            desc = step.action.description or step.action.tool_name or "?"
            lines.append(f"  {status} {desc}")

        if self._scratchpad.keys():
            lines.append(
                f"Data collected: {', '.join(self._scratchpad.keys())}"
            )
        return "\n".join(lines)

    # =====================================================================
    #  Feature 6 — Error Recovery
    # =====================================================================

    async def _handle_step_error(
        self,
        action: AgentAction,
        error: str,
    ) -> str:
        """When a step fails, ask LLM: retry / skip / abort.

        Returns one of ``"retry"``, ``"skip"``, ``"abort"``.
        """
        from jarvis.llm.model_provider import ChatMessage

        prompt = (
            f"Step failed with error: {error}\n"
            f"Tool: {action.tool_name}, args: {json.dumps(action.tool_args)}\n"
            f"Description: {action.description}\n"
            f"Options: retry, skip, abort.\n"
            f"Respond with exactly ONE word — the chosen option."
        )
        try:
            response = await self._provider.chat(
                [ChatMessage(role="user", content=prompt)],
                temperature=0.2,
                max_tokens=32,
            )
            raw = response.content.strip().lower()
            decision = raw.split()[0] if raw else "skip"
        except Exception as exc:
            log.warning(
                "LLM error-recovery call failed: %s — defaulting to skip",
                exc,
            )
            decision = "skip"

        valid = {"retry", "skip", "abort"}
        if decision not in valid:
            log.warning(
                "LLM returned invalid recovery decision %r — defaulting to skip",
                decision,
            )
            decision = "skip"

        log.info(
            "Error recovery decision for %s: %s", action.tool_name, decision,
        )
        return decision

    # =====================================================================
    #  Feature 7 — Human-in-the-Loop
    # =====================================================================

    async def _ask_user(
        self,
        question: str,
        options: list[str] | None = None,
    ) -> str:
        """Ask the user a question and wait for an answer.

        Tries ``ask_fn`` first, then falls back to ``confirm_fn``
        with a synthetic :class:`AgentAction`.

        Returns
        -------
        str
            The user's answer, or ``"default"`` on timeout / error /
            missing callback.
        """
        # Preferred path: dedicated ask_fn
        if self._ask_fn is not None:
            try:
                result = self._ask_fn(question, options)
                if asyncio.iscoroutine(result):
                    return str(await asyncio.wait_for(result, timeout=120))
                return str(result)
            except TimeoutError:
                log.warning("User did not respond within 120 s — using default")
                return "default"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ask_fn raised — using default")
                return "default"

        # Fallback: re-use confirm_fn with a synthetic action
        if self._confirm is not None:
            try:
                synthetic = AgentAction(
                    type="ask_user",
                    description=question,
                    question=question,
                    options=options or [],
                )
                result = self._confirm(synthetic)
                if asyncio.iscoroutine(result):
                    result = await result
                return str(result) if result else "default"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("confirm_fn raised during ask_user — using default")
                return "default"

        log.info("No ask_fn or confirm_fn registered; returning 'default'")
        return "default"
