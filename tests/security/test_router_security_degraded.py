"""Security degraded-mode tests (Задача 1).

When ToolGuard or OutputSanitizer fail to construct, the router must NOT
silently run unguarded. It must:
  * log an ERROR and set ``_security_degraded``;
  * fail CLOSED on the LLM execution path for network/exec tools when the
    guard is gone (but still allow read-only, non-network tools);
  * leave the sanitizer soft (its failure blocks nothing) but still ERROR;
  * never affect user voice-pattern commands (they take a different path).
"""

from __future__ import annotations

import copy
import logging

import pytest

from jarvis.core.config import ToolsConfig
from jarvis.llm.conversation import Conversation
from jarvis.llm.intent_router import IntentRouter, SpeakIntent
from jarvis.llm.ollama_client import ChatChunk
from jarvis.tools.registry import EmptyArgs, ToolRegistry, ToolResult
from jarvis.tools.risk import RiskLevel

# --- harness ---------------------------------------------------------


class ScriptedOllama:
    def __init__(self, rounds: list[list[ChatChunk]]) -> None:
        self.rounds = rounds
        self.seen_messages: list[list[dict]] = []

    async def stream_chat(self, messages, *, tools=None):
        index = min(len(self.seen_messages), len(self.rounds) - 1)
        self.seen_messages.append(copy.deepcopy(messages))
        for chunk in self.rounds[index]:
            yield chunk


def _calls(*names_and_args, content: str = "") -> ChatChunk:
    return ChatChunk(
        content=content,
        tool_calls=tuple(
            {"function": {"name": n, "arguments": a}} for n, a in names_and_args
        ),
        done=True,
    )


def _text(text: str, *, done: bool = False) -> ChatChunk:
    return ChatChunk(content=text, done=done)


class _ScriptedTool:
    args_schema = EmptyArgs
    requires_confirmation = False

    def __init__(self, name: str, result: ToolResult,
                 risk_level: RiskLevel | None = None) -> None:
        self.name = name
        self.description = f"Scripted {name}."
        self._result = result
        self.calls = 0
        if risk_level is not None:
            self.risk_level = risk_level

    async def execute(self, args) -> ToolResult:
        self.calls += 1
        return self._result


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry(ToolsConfig())
    for t in tools:
        reg.register(t)
    return reg


async def _collect(gen) -> list:
    return [x async for x in gen]


def _router_with_failing(llm, registry, *, fail: str):
    """Build a router with ToolGuard or OutputSanitizer construction forced
    to raise. ``fail`` is 'guard' or 'sanitizer'.
    """
    from unittest.mock import patch

    conv = Conversation(system_prompt_provider=lambda: "sys")
    target = {
        "guard": "jarvis.security.tool_guard.ToolGuard",
        "sanitizer": "jarvis.security.output_sanitizer.OutputSanitizer",
    }[fail]
    with patch(target, side_effect=RuntimeError("boom")):
        router = IntentRouter(llm=llm, conversation=conv, registry=registry)
    return router, conv


# --- ToolGuard construction failure ----------------------------------


@pytest.mark.asyncio
async def test_guard_failure_blocks_network_tool(caplog):
    net = _ScriptedTool("open_url", ToolResult(success=True, output="ok"))
    llm = ScriptedOllama([
        [_calls(("open_url", {"url": "http://x"}))],
        [_text("done", done=True)],
    ])
    with caplog.at_level(logging.ERROR):
        router, _ = _router_with_failing(llm, _registry(net), fail="guard")
        await _collect(router.route("open the site"))

    assert router._security_degraded is True
    assert net.calls == 0, "network tool must be blocked when guard is down"
    assert any("DEGRADED MODE" in r.message or "guard unavailable" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_guard_failure_blocks_exec_tool():
    code = _ScriptedTool(
        "code_runner", ToolResult(success=True, output="ran"),
    )
    llm = ScriptedOllama([
        [_calls(("code_runner", {"action": "run_script", "path": "x.py"}))],
        [_text("done", done=True)],
    ])
    router, _ = _router_with_failing(llm, _registry(code), fail="guard")
    await _collect(router.route("run the script"))
    assert code.calls == 0, "exec tool must be blocked when guard is down"


@pytest.mark.asyncio
async def test_guard_failure_allows_readonly_nonnetwork_tool():
    # search_memory is RiskLevel.READ and not in NETWORK_TOOLS/EXEC_TOOLS.
    mem = _ScriptedTool(
        "search_memory", ToolResult(success=True, output="found"),
        risk_level=RiskLevel.READ,
    )
    llm = ScriptedOllama([
        [_calls(("search_memory", {"query": "cats"}))],
        [_text("done", done=True)],
    ])
    router, _ = _router_with_failing(llm, _registry(mem), fail="guard")
    await _collect(router.route("what do you remember"))
    assert mem.calls == 1, "read-only non-network tool should still run"


# --- OutputSanitizer construction failure (soft) ---------------------


@pytest.mark.asyncio
async def test_sanitizer_failure_blocks_nothing_but_logs(caplog):
    net = _ScriptedTool("open_url", ToolResult(success=True, output="ok"))
    llm = ScriptedOllama([
        [_calls(("open_url", {"url": "http://x"}))],
        [_text("done", done=True)],
    ])
    with caplog.at_level(logging.ERROR):
        # Sanitizer fails, but ToolGuard is real → open_url is allowed by the
        # guard (no leak in a bare URL) and executes. The point: sanitizer
        # failure did not block, but it WAS logged as ERROR.
        router, _ = _router_with_failing(llm, _registry(net), fail="sanitizer")
        await _collect(router.route("open the site"))

    assert router._security_degraded is True
    assert router._sanitizer is None
    assert any("OutputSanitizer" in r.message and r.levelno == logging.ERROR
               for r in caplog.records)


# --- user voice-pattern commands unaffected --------------------------


@pytest.mark.asyncio
async def test_voice_pattern_command_works_with_guard_down():
    """A pattern-matched user command returns before the LLM/_run_tools
    path, so a down ToolGuard does not touch it."""
    llm = ScriptedOllama([[_text("unused", done=True)]])
    router, _ = _router_with_failing(llm, _registry(), fail="guard")
    # "what time is it" matches the get_time voice pattern, which answers
    # with a SpeakIntent directly in _try_pattern — before any LLM round or
    # _run_tools, so ToolGuard is never consulted. The LLM script is never
    # reached (len(llm.seen_messages) stays 0).
    intents = await _collect(router.route("what time is it"))
    assert any(isinstance(i, SpeakIntent) for i in intents), (
        "user voice command should still answer with guard down"
    )
    assert len(llm.seen_messages) == 0, "pattern path must not hit the LLM"
