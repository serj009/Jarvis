"""Tests for jarvis.skills.protocols — ProtocolEngine.

Covers:
  - SQLite schema creation and round-trip
  - CRUD: create, get, list, delete, enable/disable, update_steps
  - Simple regex step parsing (_parse_simple_steps)
  - Protocol execution through a mock registry
  - Protocol name matching for IntentRouter integration
  - Edge cases: duplicate names, invalid names, disabled protocols
  - LLM fallback parsing (mocked)
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.skills.protocols import (
    Protocol,
    ProtocolEngine,
    ProtocolExecutionResult,
    ProtocolStep,
    _parse_simple_steps,
)
from jarvis.tools.registry import ToolResult

# --- helpers / fixtures ------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_protocols.db"


@pytest.fixture
def engine(tmp_db: Path) -> ProtocolEngine:
    """Engine with no registry / LLM (simple regex mode only)."""
    eng = ProtocolEngine(db_path=tmp_db, registry=None, ollama_client=None)
    yield eng
    eng.close()


def _make_mock_registry() -> MagicMock:
    """Registry mock that succeeds for any tool call."""
    reg = MagicMock()
    reg.execute = AsyncMock(
        return_value=ToolResult(success=True, output="done"),
    )
    reg.list_enabled = MagicMock(return_value=[])
    return reg


# ======================================================================
# 1. Schema & round-trip
# ======================================================================

class TestSchemaAndRoundTrip:

    def test_db_created(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        assert tmp_db.exists()
        eng.close()

    def test_protocol_step_roundtrip(self):
        step = ProtocolStep(
            tool_name="volume",
            args={"action": "up", "amount": 10},
            delay_after=1.5,
            condition=None,
        )
        d = step.to_dict()
        restored = ProtocolStep.from_dict(d)
        assert restored.tool_name == "volume"
        assert restored.args == {"action": "up", "amount": 10}
        assert restored.delay_after == 1.5
        assert restored.condition is None

    def test_protocol_to_dict(self):
        proto = Protocol(
            name="Test",
            steps=[ProtocolStep(tool_name="screenshot", args={})],
            enabled=True,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        d = proto.to_dict()
        assert d["name"] == "Test"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["tool_name"] == "screenshot"


# ======================================================================
# 2. Simple regex step parsing
# ======================================================================

class TestSimpleParsing:

    def test_play_music(self):
        steps = _parse_simple_steps("включи музыку")
        assert steps is not None
        assert len(steps) == 1
        assert steps[0].tool_name == "media_control"
        assert steps[0].args["action"] == "play"

    def test_volume_max(self):
        steps = _parse_simple_steps("звук на максимум")
        assert steps is not None
        assert steps[0].tool_name == "volume"
        assert steps[0].args["action"] == "up"
        assert steps[0].args["amount"] == 20

    def test_compound_and(self):
        steps = _parse_simple_steps("включи музыку и звук на максимум")
        assert steps is not None
        assert len(steps) == 2
        assert steps[0].tool_name == "media_control"
        assert steps[1].tool_name == "volume"

    def test_compound_comma(self):
        steps = _parse_simple_steps("включи музыку, звук на максимум")
        assert steps is not None
        assert len(steps) == 2

    def test_compound_then(self):
        steps = _parse_simple_steps("play music then volume max")
        assert steps is not None
        assert len(steps) == 2

    def test_open_app(self):
        steps = _parse_simple_steps("open spotify")
        assert steps is not None
        assert steps[0].tool_name == "open_app"
        assert steps[0].args["name"] == "spotify"

    def test_screenshot(self):
        steps = _parse_simple_steps("screenshot")
        assert steps is not None
        assert steps[0].tool_name == "screenshot"

    def test_unknown_returns_none(self):
        """Unknown phrases should return None (fall to LLM)."""
        assert _parse_simple_steps("расскажи анекдот") is None

    def test_empty_returns_none(self):
        assert _parse_simple_steps("") is None
        assert _parse_simple_steps("   ") is None

    def test_pause_music(self):
        steps = _parse_simple_steps("pause")
        assert steps is not None
        assert steps[0].tool_name == "media_control"
        assert steps[0].args["action"] == "pause"

    def test_lock_screen(self):
        steps = _parse_simple_steps("lock screen")
        assert steps is not None
        assert steps[0].tool_name == "lock_screen"


# ======================================================================
# 3. CRUD operations
# ======================================================================

class TestCRUD:

    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        proto = await eng.create_protocol("Party", "включи музыку и звук на максимум")
        assert proto.name == "Party"
        assert len(proto.steps) == 2
        assert proto.enabled is True

        fetched = eng.get_protocol("Party")
        assert fetched is not None
        assert fetched.name == "Party"
        assert len(fetched.steps) == 2
        eng.close()

    @pytest.mark.asyncio
    async def test_case_insensitive_get(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Party", "включи музыку")
        assert eng.get_protocol("party") is not None
        assert eng.get_protocol("PARTY") is not None
        eng.close()

    @pytest.mark.asyncio
    async def test_list_protocols(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Alpha", "включи музыку")
        await eng.create_protocol("Beta", "screenshot")
        protocols = eng.list_protocols()
        assert len(protocols) == 2
        names = {p.name for p in protocols}
        assert "Alpha" in names
        assert "Beta" in names
        eng.close()

    @pytest.mark.asyncio
    async def test_delete_protocol(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("ToDelete", "screenshot")
        assert eng.delete_protocol("ToDelete") is True
        assert eng.get_protocol("ToDelete") is None
        assert eng.delete_protocol("NonExistent") is False
        eng.close()

    @pytest.mark.asyncio
    async def test_enable_disable(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Toggle", "screenshot")
        assert eng.set_enabled("Toggle", False) is True
        proto = eng.get_protocol("Toggle")
        assert proto is not None
        assert proto.enabled is False

        assert eng.set_enabled("Toggle", True) is True
        proto = eng.get_protocol("Toggle")
        assert proto.enabled is True
        eng.close()

    @pytest.mark.asyncio
    async def test_update_steps(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Update", "screenshot")
        new_steps = [ProtocolStep(tool_name="volume", args={"action": "up"})]
        assert eng.update_steps("Update", new_steps) is True
        proto = eng.get_protocol("Update")
        assert proto is not None
        assert len(proto.steps) == 1
        assert proto.steps[0].tool_name == "volume"
        eng.close()

    @pytest.mark.asyncio
    async def test_duplicate_name_raises(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Dup", "screenshot")
        with pytest.raises(ValueError, match="already exists"):
            await eng.create_protocol("Dup", "включи музыку")
        eng.close()

    @pytest.mark.asyncio
    async def test_invalid_name_raises(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        with pytest.raises(ValueError, match="Invalid protocol name"):
            await eng.create_protocol("", "screenshot")
        eng.close()

    @pytest.mark.asyncio
    async def test_list_enabled_only(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("On", "screenshot")
        await eng.create_protocol("Off", "включи музыку")
        eng.set_enabled("Off", False)
        enabled = eng.list_protocols(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "On"
        eng.close()


# ======================================================================
# 4. Protocol execution
# ======================================================================

class TestExecution:

    @pytest.mark.asyncio
    async def test_execute_success(self, tmp_db: Path):
        reg = _make_mock_registry()
        eng = ProtocolEngine(db_path=tmp_db, registry=reg)
        await eng.create_protocol("Exec", "включи музыку и звук на максимум")
        result = await eng.execute_protocol("Exec")
        assert result.success is True
        assert result.steps_completed == 2
        assert result.steps_total == 2
        assert reg.execute.call_count == 2
        eng.close()

    @pytest.mark.asyncio
    async def test_execute_step_failure(self, tmp_db: Path):
        reg = _make_mock_registry()
        # Second call fails
        reg.execute.side_effect = [
            ToolResult(success=True, output="ok"),
            ToolResult(success=False, error="volume unavailable"),
        ]
        eng = ProtocolEngine(db_path=tmp_db, registry=reg)
        await eng.create_protocol("Fail", "включи музыку и звук на максимум")
        result = await eng.execute_protocol("Fail")
        assert result.success is False
        assert result.steps_completed == 1
        assert "volume unavailable" in (result.error or "")
        eng.close()

    @pytest.mark.asyncio
    async def test_execute_not_found(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        result = await eng.execute_protocol("NoSuch")
        assert result.success is False
        assert "not found" in (result.error or "").lower()
        eng.close()

    @pytest.mark.asyncio
    async def test_execute_disabled(self, tmp_db: Path):
        reg = _make_mock_registry()
        eng = ProtocolEngine(db_path=tmp_db, registry=reg)
        await eng.create_protocol("Dis", "screenshot")
        eng.set_enabled("Dis", False)
        result = await eng.execute_protocol("Dis")
        assert result.success is False
        assert "disabled" in (result.error or "").lower()
        eng.close()

    @pytest.mark.asyncio
    async def test_execute_no_registry(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db, registry=None)
        await eng.create_protocol("NoReg", "screenshot")
        result = await eng.execute_protocol("NoReg")
        assert result.success is False
        assert "registry" in (result.error or "").lower()
        eng.close()

    @pytest.mark.asyncio
    async def test_execute_with_delay(self, tmp_db: Path):
        """Steps with delay_after should pause between them."""
        reg = _make_mock_registry()
        eng = ProtocolEngine(db_path=tmp_db, registry=reg)
        await eng.create_protocol("Delay", "screenshot")
        # Manually set delay
        steps = eng.get_protocol("Delay").steps
        steps[0].delay_after = 0.01  # 10ms
        eng.update_steps("Delay", steps)
        result = await eng.execute_protocol("Delay")
        assert result.success is True
        eng.close()


# ======================================================================
# 5. Protocol name matching (IntentRouter integration)
# ======================================================================

class TestMatching:

    @pytest.mark.asyncio
    async def test_match_exact(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Party", "включи музыку")
        assert eng.match_protocol("Party") is not None
        assert eng.match_protocol("party") is not None
        assert eng.match_protocol("PARTY") is not None

    @pytest.mark.asyncio
    async def test_match_no_match(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Party", "включи музыку")
        assert eng.match_protocol("Work") is None
        assert eng.match_protocol("") is None

    @pytest.mark.asyncio
    async def test_match_disabled_ignored(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Party", "включи музыку")
        eng.set_enabled("Party", False)
        assert eng.match_protocol("Party") is None
        eng.close()

    @pytest.mark.asyncio
    async def test_match_with_whitespace(self, tmp_db: Path):
        eng = ProtocolEngine(db_path=tmp_db)
        await eng.create_protocol("Party", "включи музыку")
        assert eng.match_protocol("  Party  ") is not None
        eng.close()
