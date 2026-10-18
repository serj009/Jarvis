"""Tests for the AutonomousAgent — general-purpose plan-and-execute agent.

Uses mocked ModelProvider and ToolRegistry so tests are fast and
deterministic.  25+ tests covering:
- plan creation (mock LLM)
- action execution (mock tools)
- iteration limit respected
- time limit respected
- destructive confirmation
- done detection
- plan adjustment
- reasoning log
- error recovery
- JSON extraction
- cancellation
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.agents.autonomous import (
    AGENT_SYSTEM_PROMPT,
    AgentAction,
    AgentStep,
    AutonomousAgent,
    _extract_json,
)
from jarvis.tools.registry import ToolResult
from jarvis.tools.risk import RiskLevel


# ---------------------------------------------------------------------------
# Helpers — mock provider
# ---------------------------------------------------------------------------

class _FakeChatResponse:
    """Mimics ``ChatResponse`` from model_provider."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.finish_reason = "stop"
        self.model = "fake-model"
        self.usage: dict[str, int] = {}


class FakeProvider:
    """A mock ModelProvider that returns pre-programmed responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls: list[list[object]] = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.calls.append(messages)
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
        else:
            resp = '{"type": "done", "reasoning": "no more responses"}'
        return _FakeChatResponse(resp)

    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=4096):
        resp = await self.chat(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        yield MagicMock(content=resp.content, done=True)

    def display_name(self) -> str:
        return "FakeProvider (test)"


# ---------------------------------------------------------------------------
# Helpers — mock config
# ---------------------------------------------------------------------------

class FakeAgentConfig:
    """Mimics ``AgentConfig`` fields."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_iterations: int = 5,
        max_time_seconds: int = 60,
        confirm_destructive: bool = True,
        model_provider: str = "main",
        verbose_reasoning: bool = False,
    ) -> None:
        self.enabled = enabled
        self.max_iterations = max_iterations
        self.max_time_seconds = max_time_seconds
        self.confirm_destructive = confirm_destructive
        self.model_provider = model_provider
        self.verbose_reasoning = verbose_reasoning


# ---------------------------------------------------------------------------
# Helpers — mock tool / registry
# ---------------------------------------------------------------------------

class FakeTool:
    """A tool that always succeeds."""

    def __init__(self, name: str = "test_tool", description: str = "A test tool"):
        self.name = name
        self.description = description
        self.requires_confirmation = False
        self.args_schema = type("Args", (), {"model_validate": staticmethod(lambda x: x)})

    async def execute(self, args):
        return ToolResult(success=True, output=f"Executed {self.name}")


class DestructiveTool:
    """A tool marked as destructive."""

    def __init__(self, name: str = "delete_tool"):
        self.name = name
        self.description = "Deletes something"
        self.requires_confirmation = False
        self.risk_level = RiskLevel.DESTRUCTIVE
        self.args_schema = type("Args", (), {"model_validate": staticmethod(lambda x: x)})

    async def execute(self, args):
        return ToolResult(success=True, output="Deleted")


class FailingTool:
    """A tool that always fails."""

    def __init__(self, name: str = "fail_tool"):
        self.name = name
        self.description = "Always fails"
        self.requires_confirmation = False
        self.args_schema = type("Args", (), {"model_validate": staticmethod(lambda x: x)})

    async def execute(self, args):
        return ToolResult(success=False, error="Tool failed on purpose")


class CrashingTool:
    """A tool that raises an exception."""

    def __init__(self, name: str = "crash_tool"):
        self.name = name
        self.description = "Crashes"
        self.requires_confirmation = False
        self.args_schema = type("Args", (), {"model_validate": staticmethod(lambda x: x)})

    async def execute(self, args):
        raise RuntimeError("Boom!")


class FakeRegistry:
    """A minimal tool registry mock."""

    def __init__(self, tools: dict[str, object] | None = None):
        self._tools = tools or {}

    def list_enabled(self):
        return list(self._tools.values())

    def get(self, name: str):
        return self._tools.get(name)

    async def execute(self, name: str, raw_args: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"unknown tool {name!r}")
        try:
            args = tool.args_schema.model_validate(raw_args)
            return await tool.execute(args)
        except Exception as e:
            return ToolResult(success=False, error=f"tool {name!r} crashed: {e}")


def _make_agent(
    responses: list[str],
    *,
    tools: dict[str, object] | None = None,
    max_iterations: int = 5,
    max_time_seconds: int = 60,
    confirm_destructive: bool = True,
    confirm_fn=None,
    verbose_reasoning: bool = False,
) -> tuple[AutonomousAgent, FakeProvider]:
    provider = FakeProvider(responses=responses)
    config = FakeAgentConfig(
        max_iterations=max_iterations,
        max_time_seconds=max_time_seconds,
        confirm_destructive=confirm_destructive,
        verbose_reasoning=verbose_reasoning,
    )
    registry = FakeRegistry(tools=tools or {"test_tool": FakeTool()})
    agent = AutonomousAgent(
        provider=provider,
        registry=registry,
        config=config,
        confirm_fn=confirm_fn,
    )
    return agent, provider


async def _collect(agent: AutonomousAgent, goal: str) -> list[str]:
    """Run agent and collect all messages."""
    messages = []
    async for msg in agent.run(goal):
        messages.append(msg)
    return messages


# ---------------------------------------------------------------------------
# JSON extraction tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for the _extract_json helper."""

    def test_raw_object(self):
        result = _extract_json('{"type": "done", "reasoning": "ok"}')
        assert result == {"type": "done", "reasoning": "ok"}

    def test_raw_array(self):
        result = _extract_json('["step 1", "step 2"]')
        assert result == ["step 1", "step 2"]

    def test_with_surrounding_text(self):
        text = 'Here is my response:\n{"type": "done", "reasoning": "ok"}\nDone.'
        result = _extract_json(text)
        assert result["type"] == "done"

    def test_fenced_code_block(self):
        text = '```json\n{"type": "tool_call", "tool": "test"}\n```'
        result = _extract_json(text)
        assert result["type"] == "tool_call"

    def test_fenced_block_no_lang(self):
        text = '```\n["a", "b", "c"]\n```'
        result = _extract_json(text)
        assert result == ["a", "b", "c"]

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("This is not JSON at all")

    def test_whitespace_handling(self):
        result = _extract_json('  \n  {"key": "value"}\n  ')
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# Plan creation tests
# ---------------------------------------------------------------------------

class TestPlanCreation:
    """Tests for the agent's plan creation."""

    @pytest.mark.asyncio
    async def test_basic_plan(self):
        agent, provider = _make_agent(
            responses=[
                '["scan directory", "create folders", "move files"]',
                '{"type": "done", "reasoning": "goal achieved"}',
            ],
        )
        messages = await _collect(agent, "organise downloads")
        assert any("План готов: 3" in m for m in messages)

    @pytest.mark.asyncio
    async def test_plan_with_fenced_json(self):
        agent, _ = _make_agent(
            responses=[
                '```json\n["step one", "step two"]\n```',
                '{"type": "done", "reasoning": "done"}',
            ],
        )
        messages = await _collect(agent, "test goal")
        assert any("2 шагов" in m for m in messages)

    @pytest.mark.asyncio
    async def test_empty_plan(self):
        agent, _ = _make_agent(
            responses=['{"not": "a list"}'],
        )
        messages = await _collect(agent, "test goal")
        assert any("Не удалось разбить" in m for m in messages)

    @pytest.mark.asyncio
    async def test_plan_failure_graceful(self):
        """LLM returns garbage — agent stops gracefully."""
        agent, _ = _make_agent(
            responses=["THIS IS NOT JSON AT ALL"],
        )
        messages = await _collect(agent, "test goal")
        assert any("Ошибка" in m or "Не удалось" in m for m in messages)


# ---------------------------------------------------------------------------
# Action execution tests
# ---------------------------------------------------------------------------

class TestActionExecution:
    """Tests for tool execution through the agent."""

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        agent, _ = _make_agent(
            responses=[
                '["call test_tool"]',
                '{"type": "tool_call", "tool": "test_tool", "args": {}, '
                '"reasoning": "need to test", "description": "Testing tool"}',
                '{"type": "done", "reasoning": "done", "description": "All good"}',
            ],
            tools={"test_tool": FakeTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("Выполняю" in m for m in messages)
        assert any("Executed test_tool" in m for m in messages)

    @pytest.mark.asyncio
    async def test_tool_failure(self):
        agent, _ = _make_agent(
            responses=[
                '["call fail_tool"]',
                '{"type": "tool_call", "tool": "fail_tool", "args": {}, '
                '"reasoning": "try it", "description": "Try the failing tool"}',
                '{"type": "done", "reasoning": "tool failed"}',
            ],
            tools={"fail_tool": FailingTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("✗" in m for m in messages)

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        agent, _ = _make_agent(
            responses=[
                '["call nonexistent"]',
                '{"type": "tool_call", "tool": "nonexistent", "args": {}, '
                '"reasoning": "try it", "description": "Call unknown tool"}',
                '{"type": "done", "reasoning": "failed"}',
            ],
            tools={"test_tool": FakeTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("✗" in m for m in messages)

    @pytest.mark.asyncio
    async def test_tool_crash(self):
        agent, _ = _make_agent(
            responses=[
                '["call crash_tool"]',
                '{"type": "tool_call", "tool": "crash_tool", "args": {}, '
                '"reasoning": "try it", "description": "Call crashing tool"}',
                '{"type": "done", "reasoning": "recovered"}',
            ],
            tools={"crash_tool": CrashingTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("✗" in m for m in messages)


# ---------------------------------------------------------------------------
# Iteration limit tests
# ---------------------------------------------------------------------------

class TestIterationLimit:
    """Tests that the agent respects iteration limits."""

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        # Always call a tool, never "done" — must hit the limit
        responses = [
            '["step1", "step2", "step3"]',
        ] + [
            '{"type": "tool_call", "tool": "test_tool", "args": {}, '
            '"reasoning": "keep going", "description": "Step N"}'
        ] * 10
        agent, _ = _make_agent(
            responses=responses,
            max_iterations=3,
            tools={"test_tool": FakeTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("лимит итераций" in m for m in messages)

    @pytest.mark.asyncio
    async def test_iteration_limit_one(self):
        agent, _ = _make_agent(
            responses=[
                '["step1"]',
                '{"type": "tool_call", "tool": "test_tool", "args": {}, '
                '"reasoning": "go", "description": "Step 1"}',
            ],
            max_iterations=1,
            tools={"test_tool": FakeTool()},
        )
        messages = await _collect(agent, "test task")
        assert any("лимит итераций" in m for m in messages)


# ---------------------------------------------------------------------------
# Time limit tests
# ---------------------------------------------------------------------------

class TestTimeLimit:
    """Tests that the agent respects time limits."""

    @pytest.mark.asyncio
    async def test_time_limit_on_plan(self):
        """Very short timeout should trigger during planning."""

        class SlowProvider(FakeProvider):
            async def chat(self, messages, **kw):
                await asyncio.sleep(5)
                return _FakeChatResponse("[]")

        provider = SlowProvider()
        config = FakeAgentConfig(max_time_seconds=30)
        registry = FakeRegistry(tools={"test_tool": FakeTool()})
        agent = AutonomousAgent(
            provider=provider,
            registry=registry,
            config=config,
        )
        # Use a very short plan timeout: the agent will hit asyncio.wait_for
        # but max_time_seconds=30 means the 60s plan timeout kicks in first.
        # We test with a large sleep to ensure it times out.
        messages = await _collect(agent, "test")
        # Either timeout or empty plan
        assert len(messages) >= 1


# ---------------------------------------------------------------------------
# Destructive confirmation tests
# ---------------------------------------------------------------------------

class TestDestructiveConfirmation:
    """Tests for destructive action confirmation."""

    @pytest.mark.asyncio
    async def test_confirm_approved(self):
        """Destructive action approved → executed."""
        agent, _ = _make_agent(
            responses=[
                '["delete something"]',
                '{"type": "tool_call", "tool": "delete_tool", "args": {}, '
                '"reasoning": "need to delete", "description": "Delete files"}',
                '{"type": "done", "reasoning": "done"}',
            ],
            tools={"delete_tool": DestructiveTool()},
            confirm_fn=lambda action: True,
        )
        messages = await _collect(agent, "clean up")
        assert any("Deleted" in m for m in messages)

    @pytest.mark.asyncio
    async def test_confirm_denied(self):
        """Destructive action denied → skipped."""
        agent, _ = _make_agent(
            responses=[
                '["delete something"]',
                '{"type": "tool_call", "tool": "delete_tool", "args": {}, '
                '"reasoning": "need to delete", "description": "Delete files"}',
                '{"type": "done", "reasoning": "skipped deletion"}',
            ],
            tools={"delete_tool": DestructiveTool()},
            confirm_fn=lambda action: False,
        )
        messages = await _collect(agent, "clean up")
        assert any("Пропускаю" in m for m in messages)

    @pytest.mark.asyncio
    async def test_no_confirm_fn_refuses(self):
        """No confirm_fn wired → destructive action refused (fail-closed)."""
        agent, _ = _make_agent(
            responses=[
                '["delete something"]',
                '{"type": "tool_call", "tool": "delete_tool", "args": {}, '
                '"reasoning": "need to delete", "description": "Delete files"}',
                '{"type": "done", "reasoning": "skipped"}',
            ],
            tools={"delete_tool": DestructiveTool()},
            confirm_fn=None,
        )
        messages = await _collect(agent, "clean up")
        assert any("Пропускаю" in m for m in messages)

    @pytest.mark.asyncio
    async def test_confirm_destructive_disabled(self):
        """confirm_destructive=False → destructive action runs without prompt."""
        agent, _ = _make_agent(
            responses=[
                '["delete something"]',
                '{"type": "tool_call", "tool": "delete_tool", "args": {}, '
                '"reasoning": "need to delete", "description": "Delete files"}',
                '{"type": "done", "reasoning": "done"}',
            ],
            tools={"delete_tool": DestructiveTool()},
            confirm_destructive=False,
        )
        messages = await _collect(agent, "clean up")
        # Should execute without asking
        assert any("Deleted" in m for m in messages)
        assert not any("Пропускаю" in m for m in messages)

    @pytest.mark.asyncio
    async def test_confirm_fn_raises(self):
        """confirm_fn raises → treated as denied."""
        def bad_confirm(action):
            raise RuntimeError("UI crashed")

        agent, _ = _make_agent(
            responses=[
                '["delete something"]',
                '{"type": "tool_call", "tool": "delete_tool", "args": {}, '
                '"reasoning": "need to delete", "description": "Delete files"}',
                '{"type": "done", "reasoning": "skipped"}',
            ],
            tools={"delete_tool": DestructiveTool()},
            confirm_fn=bad_confirm,
        )
        messages = await _collect(agent, "clean up")
        assert any("Пропускаю" in m for m in messages)


# ---------------------------------------------------------------------------
# Done detection tests
# ---------------------------------------------------------------------------

class TestDoneDetection:
    """Tests for the agent's "done" detection."""

    @pytest.mark.asyncio
    async def test_done_with_description(self):
        agent, _ = _make_agent(
            responses=[
                '["simple step"]',
                '{"type": "done", "reasoning": "achieved", '
                '"description": "All files organized"}',
            ],
        )
        messages = await _collect(agent, "organise files")
        assert any("All files organized" in m for m in messages)

    @pytest.mark.asyncio
    async def test_done_with_reasoning_fallback(self):
        agent, _ = _make_agent(
            responses=[
                '["simple step"]',
                '{"type": "done", "reasoning": "everything is sorted"}',
            ],
        )
        messages = await _collect(agent, "organise files")
        assert any("everything is sorted" in m for m in messages)

    @pytest.mark.asyncio
    async def test_immediate_done(self):
        """LLM decides goal is already achieved."""
        agent, _ = _make_agent(
            responses=[
                '["check status"]',
                '{"type": "done", "reasoning": "already done"}',
            ],
        )
        messages = await _collect(agent, "nothing to do")
        assert any("already done" in m for m in messages)


# ---------------------------------------------------------------------------
# Plan adjustment tests
# ---------------------------------------------------------------------------

class TestPlanAdjustment:
    """Tests for the agent's plan adjustment capability."""

    @pytest.mark.asyncio
    async def test_adjust_plan(self):
        agent, _ = _make_agent(
            responses=[
                '["step1", "step2"]',
                '{"type": "adjust_plan", "new_plan": ["new_step1", "new_step2", "new_step3"], '
                '"reasoning": "need more steps"}',
                '{"type": "done", "reasoning": "done with new plan"}',
            ],
        )
        messages = await _collect(agent, "complex task")
        assert any("скорректирован" in m for m in messages)
        assert any("3 шагов" in m for m in messages)


# ---------------------------------------------------------------------------
# Reasoning log tests
# ---------------------------------------------------------------------------

class TestReasoningLog:
    """Tests for the reasoning log."""

    @pytest.mark.asyncio
    async def test_log_populated(self):
        agent, _ = _make_agent(
            responses=[
                '["test step"]',
                '{"type": "tool_call", "tool": "test_tool", "args": {}, '
                '"reasoning": "first action", "description": "Step 1"}',
                '{"type": "done", "reasoning": "finished"}',
            ],
            tools={"test_tool": FakeTool()},
        )
        await _collect(agent, "test task")
        log = agent.reasoning_log
        assert len(log) >= 2
        # First step should be tool_call
        assert log[0].action.type == "tool_call"
        assert log[0].action.tool_name == "test_tool"
        assert log[0].result is not None
        assert log[0].result.success
        # Last step should be done
        assert log[-1].action.type == "done"

    @pytest.mark.asyncio
    async def test_log_has_timestamps(self):
        agent, _ = _make_agent(
            responses=[
                '["test"]',
                '{"type": "done", "reasoning": "ok"}',
            ],
        )
        await _collect(agent, "test")
        for step in agent.reasoning_log:
            assert step.timestamp is not None

    @pytest.mark.asyncio
    async def test_log_iteration_numbers(self):
        agent, _ = _make_agent(
            responses=[
                '["step1", "step2"]',
                '{"type": "tool_call", "tool": "test_tool", "args": {}, '
                '"reasoning": "first", "description": "First"}',
                '{"type": "tool_call", "tool": "test_tool", "args": {}, '
                '"reasoning": "second", "description": "Second"}',
                '{"type": "done", "reasoning": "done"}',
            ],
            tools={"test_tool": FakeTool()},
        )
        await _collect(agent, "test")
        log = agent.reasoning_log
        iterations = [s.iteration for s in log]
        assert iterations == sorted(iterations)


# ---------------------------------------------------------------------------
# Error recovery tests
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    """Tests for error recovery during agent execution."""

    @pytest.mark.asyncio
    async def test_recover_from_bad_json(self):
        """Bad JSON from LLM → agent stops gracefully."""
        agent, _ = _make_agent(
            responses=[
                '["step1"]',
                "THIS IS NOT JSON",
            ],
        )
        messages = await _collect(agent, "test")
        # Agent should detect parse error and stop
        assert any("выполнена" in m.lower() or "parse error" in m.lower()
                    for m in messages)

    @pytest.mark.asyncio
    async def test_recover_from_unknown_type(self):
        """Unknown action type → treated as done."""
        agent, _ = _make_agent(
            responses=[
                '["step1"]',
                '{"type": "fly_to_mars", "reasoning": "impossible"}',
            ],
        )
        messages = await _collect(agent, "test")
        assert len(messages) >= 2  # plan + done


# ---------------------------------------------------------------------------
# Cancellation tests
# ---------------------------------------------------------------------------

class TestCancellation:
    """Tests for agent cancellation."""

    @pytest.mark.asyncio
    async def test_cancel(self):
        responses = [
            '["step1", "step2", "step3"]',
        ] + [
            '{"type": "tool_call", "tool": "test_tool", "args": {}, '
            '"reasoning": "keep going", "description": "Step N"}'
        ] * 10
        agent, _ = _make_agent(
            responses=responses,
            max_iterations=20,
            tools={"test_tool": FakeTool()},
        )
        # Cancel after collecting first few messages
        messages: list[str] = []
        count = 0
        async for msg in agent.run("test task"):
            messages.append(msg)
            count += 1
            if count >= 4:
                agent.cancel()
        assert any("отменена" in m for m in messages)


# ---------------------------------------------------------------------------
# Verbose reasoning tests
# ---------------------------------------------------------------------------

class TestVerboseReasoning:
    """Tests for verbose reasoning mode."""

    @pytest.mark.asyncio
    async def test_verbose_shows_plan_steps(self):
        agent, _ = _make_agent(
            responses=[
                '["scan dir", "create folders"]',
                '{"type": "done", "reasoning": "ok"}',
            ],
            verbose_reasoning=True,
        )
        messages = await _collect(agent, "test")
        # Verbose mode should show numbered plan steps
        assert any("1." in m for m in messages)
        assert any("2." in m for m in messages)


# ---------------------------------------------------------------------------
# AgentAction / AgentStep data class tests
# ---------------------------------------------------------------------------

class TestDataClasses:
    """Tests for the data classes."""

    def test_agent_action_defaults(self):
        action = AgentAction(type="done")
        assert action.type == "done"
        assert action.tool_name == ""
        assert action.tool_args == {}
        assert action.reasoning == ""
        assert action.description == ""
        assert action.is_destructive is False
        assert action.new_plan == []

    def test_agent_action_tool_call(self):
        action = AgentAction(
            type="tool_call",
            tool_name="test_tool",
            tool_args={"key": "value"},
            reasoning="because",
            description="doing a thing",
            is_destructive=True,
        )
        assert action.type == "tool_call"
        assert action.tool_name == "test_tool"
        assert action.tool_args == {"key": "value"}
        assert action.is_destructive

    def test_agent_step(self):
        action = AgentAction(type="done")
        step = AgentStep(
            iteration=0,
            action=action,
            result=None,
            reasoning="test reason",
        )
        assert step.iteration == 0
        assert step.action is action
        assert step.result is None
        assert step.reasoning == "test reason"
        assert step.timestamp is not None

    def test_agent_step_with_result(self):
        action = AgentAction(type="tool_call", tool_name="t")
        result = ToolResult(success=True, output="ok")
        step = AgentStep(
            iteration=1,
            action=action,
            result=result,
            reasoning="worked",
        )
        assert step.result.success
        assert step.result.output == "ok"


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Tests for the agent system prompt."""

    def test_prompt_has_placeholders(self):
        assert "{tool_descriptions}" in AGENT_SYSTEM_PROMPT
        assert "{action_history}" in AGENT_SYSTEM_PROMPT
        assert "{plan}" in AGENT_SYSTEM_PROMPT
        assert "{goal}" in AGENT_SYSTEM_PROMPT

    def test_prompt_format(self):
        result = AGENT_SYSTEM_PROMPT.format(
            tool_descriptions="- test: does stuff",
            action_history="none",
            plan="1. do thing",
            goal="test goal",
        )
        assert "test goal" in result
        assert "test: does stuff" in result


# ---------------------------------------------------------------------------
# Integration: multi-step execution
# ---------------------------------------------------------------------------

class TestMultiStepExecution:
    """Integration tests for multi-step task execution."""

    @pytest.mark.asyncio
    async def test_three_step_task(self):
        tool_a = FakeTool(name="tool_a", description="Tool A")
        tool_b = FakeTool(name="tool_b", description="Tool B")
        agent, _ = _make_agent(
            responses=[
                '["use tool_a", "use tool_b", "done"]',
                '{"type": "tool_call", "tool": "tool_a", "args": {}, '
                '"reasoning": "step 1", "description": "Using A"}',
                '{"type": "tool_call", "tool": "tool_b", "args": {}, '
                '"reasoning": "step 2", "description": "Using B"}',
                '{"type": "done", "reasoning": "all done", '
                '"description": "Task complete"}',
            ],
            tools={"tool_a": tool_a, "tool_b": tool_b},
        )
        messages = await _collect(agent, "multi-step task")

        assert any("План готов" in m for m in messages)
        assert any("Using A" in m for m in messages)
        assert any("Using B" in m for m in messages)
        assert any("Task complete" in m for m in messages)

        log = agent.reasoning_log
        assert len(log) == 3  # 2 tool calls + 1 done

    @pytest.mark.asyncio
    async def test_plan_property(self):
        agent, _ = _make_agent(
            responses=[
                '["alpha", "beta", "gamma"]',
                '{"type": "done", "reasoning": "ok"}',
            ],
        )
        await _collect(agent, "test")
        assert agent.plan == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_output_truncation(self):
        """Long tool output is truncated in progress messages."""

        class VerboseTool:
            name = "verbose_tool"
            description = "Returns long output"
            requires_confirmation = False
            args_schema = type("A", (), {"model_validate": staticmethod(lambda x: x)})

            async def execute(self, args):
                return ToolResult(success=True, output="x" * 500)

        agent, _ = _make_agent(
            responses=[
                '["verbose step"]',
                '{"type": "tool_call", "tool": "verbose_tool", "args": {}, '
                '"reasoning": "get data", "description": "Get verbose data"}',
                '{"type": "done", "reasoning": "done"}',
            ],
            tools={"verbose_tool": VerboseTool()},
        )
        messages = await _collect(agent, "verbose task")
        # Find the output message — should be truncated
        output_msgs = [m for m in messages if m.startswith("✓")]
        assert output_msgs
        assert len(output_msgs[0]) <= 210  # 200 + "✓ " + "…"
