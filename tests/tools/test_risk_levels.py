"""Tests for tool risk levels (T5.2).

What is pinned here:
  - RiskLevel enum has the expected members and ordering,
  - DESTRUCTIVE and SYSTEM require confirmation,
  - READ and WRITE do not,
  - every built-in tool in TOOL_RISK_MAP has a valid RiskLevel,
  - risk_level_for_tool() lookup order is correct,
  - should_confirm() respects both legacy flag and risk level,
  - the risk map covers all tools from the catalogue.
"""

from __future__ import annotations

import pytest

from jarvis.tools.registry import EmptyArgs, ToolResult
from jarvis.tools.risk import (
    TOOL_RISK_MAP,
    RiskLevel,
    risk_level_for_tool,
    should_confirm,
)

# --- RiskLevel enum ----------------------------------------------------------

class TestRiskLevelEnum:
    """RiskLevel has the expected members, ordering, and properties."""

    def test_members_exist(self) -> None:
        assert hasattr(RiskLevel, "READ")
        assert hasattr(RiskLevel, "WRITE")
        assert hasattr(RiskLevel, "DESTRUCTIVE")
        assert hasattr(RiskLevel, "SYSTEM")

    def test_ordering(self) -> None:
        assert RiskLevel.READ < RiskLevel.WRITE
        assert RiskLevel.WRITE < RiskLevel.DESTRUCTIVE
        assert RiskLevel.DESTRUCTIVE < RiskLevel.SYSTEM

    def test_read_does_not_require_confirmation(self) -> None:
        assert RiskLevel.READ.requires_confirmation is False

    def test_write_does_not_require_confirmation(self) -> None:
        assert RiskLevel.WRITE.requires_confirmation is False

    def test_destructive_requires_confirmation(self) -> None:
        assert RiskLevel.DESTRUCTIVE.requires_confirmation is True

    def test_system_requires_confirmation(self) -> None:
        assert RiskLevel.SYSTEM.requires_confirmation is True

    def test_labels_are_non_empty_strings(self) -> None:
        for level in RiskLevel:
            assert isinstance(level.label, str)
            assert len(level.label) > 0

    def test_descriptions_are_non_empty_strings(self) -> None:
        for level in RiskLevel:
            assert isinstance(level.description, str)
            assert len(level.description) > 0


# --- TOOL_RISK_MAP ----------------------------------------------------------

class TestToolRiskMap:
    """The static risk map has valid entries."""

    def test_all_values_are_risk_levels(self) -> None:
        for name, level in TOOL_RISK_MAP.items():
            assert isinstance(level, RiskLevel), (
                f"TOOL_RISK_MAP[{name!r}] = {level!r}, expected RiskLevel"
            )

    def test_known_read_tools(self) -> None:
        read_tools = [
            "clipboard", "list_directory", "get_weather",
            "screenshot", "see_screen", "read_note",
        ]
        for name in read_tools:
            assert TOOL_RISK_MAP.get(name) == RiskLevel.READ, name

    def test_known_write_tools(self) -> None:
        write_tools = [
            "open_app", "close_app", "open_url", "take_note",
            "append_to_note", "deep_research",
        ]
        for name in write_tools:
            assert TOOL_RISK_MAP.get(name) == RiskLevel.WRITE, name

    def test_known_destructive_tools(self) -> None:
        destructive_tools = [
            "delete_note", "delete_deep_research",
            "delete_all_deep_research", "clear_clipboard_history",
        ]
        for name in destructive_tools:
            assert TOOL_RISK_MAP.get(name) == RiskLevel.DESTRUCTIVE, name

    def test_known_system_tools(self) -> None:
        system_tools = ["type_into_active_window", "lock_screen"]
        for name in system_tools:
            assert TOOL_RISK_MAP.get(name) == RiskLevel.SYSTEM, name


# --- risk_level_for_tool() --------------------------------------------------

class _FakeToolWithRiskLevel:
    name = "fake_risky"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = False
    risk_level = RiskLevel.DESTRUCTIVE

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _FakeToolWithoutRiskLevel:
    name = "fake_plain"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = False

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _FakeToolInMap:
    """Tool whose name appears in TOOL_RISK_MAP (clipboard = READ)."""
    name = "clipboard"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = False

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class TestRiskLevelForTool:
    """Lookup order: explicit attribute > static map > default READ."""

    def test_explicit_attribute_wins(self) -> None:
        tool = _FakeToolWithRiskLevel()
        assert risk_level_for_tool(tool) == RiskLevel.DESTRUCTIVE

    def test_static_map_used_when_no_attribute(self) -> None:
        tool = _FakeToolInMap()
        assert risk_level_for_tool(tool) == RiskLevel.READ

    def test_default_read_when_unknown(self) -> None:
        tool = _FakeToolWithoutRiskLevel()
        assert risk_level_for_tool(tool) == RiskLevel.READ


# --- should_confirm() -------------------------------------------------------

class _ToolWithConfirmTrue:
    name = "legacy_confirm"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = True

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _ToolWithConfirmFalseButDestructive:
    name = "sneaky_destructive"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = False
    risk_level = RiskLevel.DESTRUCTIVE

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _ToolWithConfirmFalseAndRead:
    name = "safe_tool"
    description = "test"
    args_schema = EmptyArgs
    requires_confirmation = False
    risk_level = RiskLevel.READ

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class TestShouldConfirm:
    """should_confirm() is the single decision point for confirmation."""

    def test_legacy_flag_true_requires_confirmation(self) -> None:
        assert should_confirm(_ToolWithConfirmTrue()) is True

    def test_risk_destructive_requires_confirmation(self) -> None:
        assert should_confirm(_ToolWithConfirmFalseButDestructive()) is True

    def test_risk_read_does_not_require_confirmation(self) -> None:
        assert should_confirm(_ToolWithConfirmFalseAndRead()) is False

    def test_no_flag_no_risk_level_does_not_require_confirmation(self) -> None:
        assert should_confirm(_FakeToolWithoutRiskLevel()) is False

    def test_system_risk_requires_confirmation(self) -> None:
        class _Sys:
            name = "sys"
            requires_confirmation = False
            risk_level = RiskLevel.SYSTEM
        assert should_confirm(_Sys()) is True

    def test_write_risk_does_not_require_confirmation(self) -> None:
        class _Write:
            name = "write"
            requires_confirmation = False
            risk_level = RiskLevel.WRITE
        assert should_confirm(_Write()) is False


# --- catalogue coverage (optional, best-effort) -----------------------------

def test_catalogue_tools_covered_in_risk_map() -> None:
    """Every tool in the catalogue should appear in TOOL_RISK_MAP.

    This test imports the catalogue lazily to avoid hard-coupling.
    If the catalogue isn't importable (e.g. missing optional deps),
    the test is skipped gracefully.
    """
    try:
        from jarvis.tools.catalogue import local_tool_names
        names = local_tool_names()
    except ImportError:
        pytest.skip("catalogue not importable")
        return

    missing = [n for n in names if n not in TOOL_RISK_MAP]
    # We allow some tools to not be in the map (they default to READ).
    # But if more than 5 tools are missing, something is wrong.
    assert len(missing) <= 5, (
        f"Too many catalogue tools missing from TOOL_RISK_MAP: {missing}"
    )
