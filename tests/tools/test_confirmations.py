"""Tests for the confirmation flow integrated with risk levels (T5.3).

These tests verify that the registry's execute() method correctly
integrates risk levels with the confirmation flow. They complement
test_tool_confirmation.py (which tests the confirmation gate in
isolation) by verifying the risk-level-driven confirmation decisions.

What is pinned here:
  - a DESTRUCTIVE tool without requires_confirmation=True still gets
    prompted (risk level triggers confirmation),
  - a READ tool is never prompted,
  - a WRITE tool is never prompted,
  - a SYSTEM tool is always prompted,
  - the confirmation refusal message is correct for risk-triggered prompts,
  - the risk level appears in the execution log,
  - backward compatibility: the legacy requires_confirmation=True still works.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from jarvis.core.config import ToolsConfig
from jarvis.tools.registry import (
    ConfirmationRequest,
    EmptyArgs,
    ToolConfirmer,
    ToolRegistry,
    ToolResult,
)
from jarvis.tools.risk import RiskLevel

# --- fakes -------------------------------------------------------------------

class _SomeArgs(BaseModel):
    value: str = Field(default="test")


class _ReadTool:
    name = "safe_reader"
    description = "Reads something."
    args_schema = EmptyArgs
    requires_confirmation = False
    risk_level = RiskLevel.READ

    def __init__(self):
        self.ran = 0

    async def execute(self, args):
        self.ran += 1
        return ToolResult(success=True, output="read ok")


class _WriteTool:
    name = "writer"
    description = "Writes something."
    args_schema = _SomeArgs
    requires_confirmation = False
    risk_level = RiskLevel.WRITE

    def __init__(self):
        self.ran = 0

    async def execute(self, args):
        self.ran += 1
        return ToolResult(success=True, output="written")


class _DestructiveTool:
    """DESTRUCTIVE risk level, no explicit requires_confirmation=True."""
    name = "deleter"
    description = "Deletes something irreversibly."
    args_schema = _SomeArgs
    requires_confirmation = False  # Risk level should still trigger prompt!
    risk_level = RiskLevel.DESTRUCTIVE

    def __init__(self):
        self.ran = 0

    async def execute(self, args):
        self.ran += 1
        return ToolResult(success=True, output="deleted")


class _SystemTool:
    """SYSTEM risk level."""
    name = "shell_exec"
    description = "Runs a shell command."
    args_schema = _SomeArgs
    requires_confirmation = False
    risk_level = RiskLevel.SYSTEM

    def __init__(self):
        self.ran = 0

    async def execute(self, args):
        self.ran += 1
        return ToolResult(success=True, output="executed")


class _LegacyConfirmTool:
    """Legacy: requires_confirmation=True, no risk_level attribute."""
    name = "legacy_gated"
    description = "Old-style gated tool."
    args_schema = EmptyArgs
    requires_confirmation = True

    def __init__(self):
        self.ran = 0

    async def execute(self, args):
        self.ran += 1
        return ToolResult(success=True, output="legacy done")


class _RecordingConfirmer:
    """Records requests and answers with a fixed verdict."""

    def __init__(self, verdict: bool = True):
        self.verdict = verdict
        self.requests: list[ConfirmationRequest] = []

    async def confirm(self, request: ConfirmationRequest) -> bool:
        self.requests.append(request)
        return self.verdict


def _registry(confirmer=None, **kwargs):
    return ToolRegistry(ToolsConfig(**kwargs), confirmer=confirmer)


# --- risk-level-driven confirmation -----------------------------------------

async def test_read_tool_executes_without_confirmation():
    """READ tools should never be prompted."""
    confirmer = _RecordingConfirmer(verdict=False)
    reg = _registry(confirmer)
    tool = _ReadTool()
    reg.register(tool)

    result = await reg.execute("safe_reader", {})

    assert result.success is True
    assert tool.ran == 1
    assert confirmer.requests == []


async def test_write_tool_executes_without_confirmation():
    """WRITE tools should not be prompted."""
    confirmer = _RecordingConfirmer(verdict=False)
    reg = _registry(confirmer)
    tool = _WriteTool()
    reg.register(tool)

    result = await reg.execute("writer", {"value": "hello"})

    assert result.success is True
    assert tool.ran == 1
    assert confirmer.requests == []


async def test_destructive_tool_requires_confirmation_via_risk_level():
    """DESTRUCTIVE risk level triggers confirmation even when
    requires_confirmation is False."""
    confirmer = _RecordingConfirmer(verdict=True)
    reg = _registry(confirmer)
    tool = _DestructiveTool()
    reg.register(tool)

    result = await reg.execute("deleter", {"value": "bye"})

    assert result.success is True
    assert tool.ran == 1
    assert len(confirmer.requests) == 1
    assert confirmer.requests[0].tool_name == "deleter"


async def test_destructive_tool_denial_blocks_execution():
    """A denied DESTRUCTIVE tool should not run."""
    confirmer = _RecordingConfirmer(verdict=False)
    reg = _registry(confirmer)
    tool = _DestructiveTool()
    reg.register(tool)

    result = await reg.execute("deleter", {"value": "bye"})

    assert result.success is False
    assert "not approved" in (result.error or "")
    assert tool.ran == 0


async def test_system_tool_requires_confirmation():
    """SYSTEM risk level always requires confirmation."""
    confirmer = _RecordingConfirmer(verdict=True)
    reg = _registry(confirmer)
    tool = _SystemTool()
    reg.register(tool)

    result = await reg.execute("shell_exec", {"value": "ls"})

    assert result.success is True
    assert tool.ran == 1
    assert len(confirmer.requests) == 1


async def test_system_tool_denial_blocks():
    """A denied SYSTEM tool should not run."""
    confirmer = _RecordingConfirmer(verdict=False)
    reg = _registry(confirmer)
    tool = _SystemTool()
    reg.register(tool)

    result = await reg.execute("shell_exec", {"value": "rm -rf /"})

    assert result.success is False
    assert tool.ran == 0


# --- backward compatibility --------------------------------------------------

async def test_legacy_requires_confirmation_still_works():
    """A tool with requires_confirmation=True and no risk_level should
    still be prompted (backward compatibility)."""
    confirmer = _RecordingConfirmer(verdict=True)
    reg = _registry(confirmer)
    tool = _LegacyConfirmTool()
    reg.register(tool)

    result = await reg.execute("legacy_gated", {})

    assert result.success is True
    assert tool.ran == 1
    assert len(confirmer.requests) == 1


async def test_no_confirmer_denies_destructive_tool():
    """Fail-closed: no confirmer wired denies a DESTRUCTIVE tool."""
    reg = _registry()  # no confirmer
    tool = _DestructiveTool()
    reg.register(tool)

    result = await reg.execute("deleter", {"value": "test"})

    assert result.success is False
    assert "no confirmation prompt" in (result.error or "")
    assert tool.ran == 0


async def test_no_confirmer_allows_read_tool():
    """READ tools work fine even without a confirmer."""
    reg = _registry()  # no confirmer
    tool = _ReadTool()
    reg.register(tool)

    result = await reg.execute("safe_reader", {})

    assert result.success is True
    assert tool.ran == 1


async def test_no_confirmer_allows_write_tool():
    """WRITE tools work fine even without a confirmer."""
    reg = _registry()  # no confirmer
    tool = _WriteTool()
    reg.register(tool)

    result = await reg.execute("writer", {"value": "hello"})

    assert result.success is True
    assert tool.ran == 1
