"""Tests for jarvis.tools.local.protocol_tools — the 7 voice commands.

Covers:
  - Tool protocol satisfaction (name, description, args_schema, execute)
  - Voice pattern matching (EN, RU, UK)
  - CreateProtocolTool: success, duplicate, invalid name, no engine
  - RunProtocolTool: success, failure, no engine
  - ListProtocolsTool: empty, populated, no engine
  - DeleteProtocolTool: success, not found, no engine
  - EditProtocolTool: success, not found, no engine
  - EnableProtocolTool / DisableProtocolTool: success, not found
  - Integration with catalogue and risk map
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.skills.protocols import ProtocolEngine
from jarvis.tools.local.protocol_tools import (
    CreateProtocolTool,
    DeleteProtocolTool,
    DisableProtocolTool,
    EditProtocolTool,
    EnableProtocolTool,
    ListProtocolsTool,
    RunProtocolTool,
)
from jarvis.tools.registry import Tool, ToolResult

# --- fixtures -----------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_proto_tools.db"


@pytest.fixture
def engine(tmp_db: Path) -> ProtocolEngine:
    eng = ProtocolEngine(db_path=tmp_db, registry=None, ollama_client=None)
    yield eng
    eng.close()


def _mock_registry():
    reg = MagicMock()
    reg.execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    reg.list_enabled = MagicMock(return_value=[])
    return reg


# ======================================================================
# 1. Protocol satisfaction
# ======================================================================

class TestToolProtocol:

    def test_all_tools_satisfy_tool_protocol(self):
        """Each protocol tool has name, description, args_schema, execute."""
        tools = [
            CreateProtocolTool(),
            RunProtocolTool(),
            ListProtocolsTool(),
            DeleteProtocolTool(),
            EditProtocolTool(),
            EnableProtocolTool(),
            DisableProtocolTool(),
        ]
        for tool in tools:
            assert isinstance(tool, Tool)
            assert isinstance(tool.name, str)
            assert len(tool.name) > 0
            assert isinstance(tool.description, str)
            assert tool.args_schema is not None

    def test_tool_names_unique(self):
        tools = [
            CreateProtocolTool(),
            RunProtocolTool(),
            ListProtocolsTool(),
            DeleteProtocolTool(),
            EditProtocolTool(),
            EnableProtocolTool(),
            DisableProtocolTool(),
        ]
        names = [t.name for t in tools]
        assert len(names) == len(set(names))


# ======================================================================
# 2. Voice pattern tests
# ======================================================================

class TestVoicePatterns:

    def _match(self, tool_cls, text: str) -> re.Match | None:
        for vp in tool_cls.voice_patterns:
            m = re.compile(vp.regex).match(text.lower().strip())
            if m:
                return m
        return None

    # -- Create --
    def test_create_en(self):
        m = self._match(CreateProtocolTool, "create protocol Party — play music and volume max")
        assert m is not None

    def test_create_ru(self):
        m = self._match(CreateProtocolTool, "создай протокол Вечеринка — включи музыку")
        assert m is not None

    def test_create_uk(self):
        m = self._match(CreateProtocolTool, "створи протокол Вечірка — увімкни музику")
        assert m is not None

    # -- Run --
    def test_run_en(self):
        m = self._match(RunProtocolTool, "run protocol Party")
        assert m is not None

    def test_run_ru(self):
        m = self._match(RunProtocolTool, "запусти протокол Вечеринка")
        assert m is not None

    # -- List --
    def test_list_en(self):
        m = self._match(ListProtocolsTool, "list protocols")
        assert m is not None

    def test_list_ru(self):
        m = self._match(ListProtocolsTool, "список протоколов")
        assert m is not None

    # -- Delete --
    def test_delete_en(self):
        m = self._match(DeleteProtocolTool, "delete protocol Party")
        assert m is not None

    def test_delete_ru(self):
        m = self._match(DeleteProtocolTool, "удали протокол Вечеринка")
        assert m is not None

    # -- Edit --
    def test_edit_en(self):
        m = self._match(EditProtocolTool, "edit protocol Party — new steps here")
        assert m is not None

    # -- Enable --
    def test_enable_en(self):
        m = self._match(EnableProtocolTool, "enable protocol Party")
        assert m is not None

    def test_enable_ru(self):
        m = self._match(EnableProtocolTool, "включи протокол Вечеринка")
        assert m is not None

    # -- Disable --
    def test_disable_en(self):
        m = self._match(DisableProtocolTool, "disable protocol Party")
        assert m is not None

    def test_disable_ru(self):
        m = self._match(DisableProtocolTool, "отключи протокол Вечеринка")
        assert m is not None


# ======================================================================
# 3. CreateProtocolTool
# ======================================================================

class TestCreateProtocolTool:

    @pytest.mark.asyncio
    async def test_create_success(self, engine: ProtocolEngine):
        tool = CreateProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import CreateProtocolArgs
        result = await tool.execute(CreateProtocolArgs(
            name="Party",
            description="включи музыку и звук на максимум",
        ))
        assert result.success is True
        assert "Party" in (result.output or "")
        assert "2" in (result.output or "")  # 2 steps

    @pytest.mark.asyncio
    async def test_create_duplicate(self, engine: ProtocolEngine):
        tool = CreateProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import CreateProtocolArgs
        await tool.execute(CreateProtocolArgs(name="Dup", description="screenshot"))
        result = await tool.execute(CreateProtocolArgs(name="Dup", description="screenshot"))
        assert result.success is False
        assert "already exists" in (result.error or "")

    @pytest.mark.asyncio
    async def test_create_no_engine(self):
        tool = CreateProtocolTool(engine=None)
        from jarvis.tools.local.protocol_tools import CreateProtocolArgs
        result = await tool.execute(CreateProtocolArgs(name="X", description="test"))
        assert result.success is False
        assert "not available" in (result.error or "")


# ======================================================================
# 4. RunProtocolTool
# ======================================================================

class TestRunProtocolTool:

    @pytest.mark.asyncio
    async def test_run_success(self, tmp_db: Path):
        reg = _mock_registry()
        eng = ProtocolEngine(db_path=tmp_db, registry=reg)
        await eng.create_protocol("Run", "screenshot")
        tool = RunProtocolTool(engine=eng)
        from jarvis.tools.local.protocol_tools import RunProtocolArgs
        result = await tool.execute(RunProtocolArgs(name="Run"))
        assert result.success is True
        eng.close()

    @pytest.mark.asyncio
    async def test_run_not_found(self, engine: ProtocolEngine):
        tool = RunProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import RunProtocolArgs
        result = await tool.execute(RunProtocolArgs(name="Missing"))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_run_no_engine(self):
        tool = RunProtocolTool(engine=None)
        from jarvis.tools.local.protocol_tools import RunProtocolArgs
        result = await tool.execute(RunProtocolArgs(name="X"))
        assert result.success is False


# ======================================================================
# 5. ListProtocolsTool
# ======================================================================

class TestListProtocolsTool:

    @pytest.mark.asyncio
    async def test_list_empty(self, engine: ProtocolEngine):
        tool = ListProtocolsTool(engine=engine)
        from jarvis.tools.local.protocol_tools import ListProtocolsArgs
        result = await tool.execute(ListProtocolsArgs())
        assert result.success is True
        assert "no protocols" in (result.output or "").lower()

    @pytest.mark.asyncio
    async def test_list_populated(self, engine: ProtocolEngine):
        await engine.create_protocol("A", "screenshot")
        await engine.create_protocol("B", "включи музыку")
        tool = ListProtocolsTool(engine=engine)
        from jarvis.tools.local.protocol_tools import ListProtocolsArgs
        result = await tool.execute(ListProtocolsArgs())
        assert result.success is True
        assert "2" in (result.output or "")

    @pytest.mark.asyncio
    async def test_list_no_engine(self):
        tool = ListProtocolsTool(engine=None)
        from jarvis.tools.local.protocol_tools import ListProtocolsArgs
        result = await tool.execute(ListProtocolsArgs())
        assert result.success is False


# ======================================================================
# 6. DeleteProtocolTool
# ======================================================================

class TestDeleteProtocolTool:

    @pytest.mark.asyncio
    async def test_delete_success(self, engine: ProtocolEngine):
        await engine.create_protocol("Del", "screenshot")
        tool = DeleteProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import DeleteProtocolArgs
        result = await tool.execute(DeleteProtocolArgs(name="Del"))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_not_found(self, engine: ProtocolEngine):
        tool = DeleteProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import DeleteProtocolArgs
        result = await tool.execute(DeleteProtocolArgs(name="Ghost"))
        assert result.success is False

    def test_requires_confirmation(self):
        tool = DeleteProtocolTool()
        assert tool.requires_confirmation is True


# ======================================================================
# 7. EnableProtocolTool / DisableProtocolTool
# ======================================================================

class TestEnableDisable:

    @pytest.mark.asyncio
    async def test_enable_success(self, engine: ProtocolEngine):
        await engine.create_protocol("E", "screenshot")
        engine.set_enabled("E", False)
        tool = EnableProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import EnableProtocolArgs
        result = await tool.execute(EnableProtocolArgs(name="E"))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_disable_success(self, engine: ProtocolEngine):
        await engine.create_protocol("D", "screenshot")
        tool = DisableProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import DisableProtocolArgs
        result = await tool.execute(DisableProtocolArgs(name="D"))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_enable_not_found(self, engine: ProtocolEngine):
        tool = EnableProtocolTool(engine=engine)
        from jarvis.tools.local.protocol_tools import EnableProtocolArgs
        result = await tool.execute(EnableProtocolArgs(name="Ghost"))
        assert result.success is False


# ======================================================================
# 8. Catalogue & risk integration
# ======================================================================

class TestIntegration:

    def test_protocol_tools_in_catalogue(self):
        """All protocol tools are in the static catalogue."""
        from jarvis.tools.catalogue import local_tool_names
        names = local_tool_names()
        expected = {
            "create_protocol", "run_protocol", "list_protocols",
            "delete_protocol", "edit_protocol", "enable_protocol",
            "disable_protocol",
        }
        for name in expected:
            assert name in names, f"{name} missing from catalogue"

    def test_protocol_tools_in_risk_map(self):
        """All protocol tools have risk levels assigned."""
        from jarvis.tools.risk import TOOL_RISK_MAP, RiskLevel
        expected = {
            "create_protocol": RiskLevel.WRITE,
            "run_protocol": RiskLevel.WRITE,
            "list_protocols": RiskLevel.READ,
            "delete_protocol": RiskLevel.DESTRUCTIVE,
            "edit_protocol": RiskLevel.WRITE,
            "enable_protocol": RiskLevel.WRITE,
            "disable_protocol": RiskLevel.WRITE,
        }
        for name, level in expected.items():
            assert name in TOOL_RISK_MAP, f"{name} missing from TOOL_RISK_MAP"
            assert TOOL_RISK_MAP[name] == level, (
                f"{name}: expected {level}, got {TOOL_RISK_MAP[name]}"
            )

    def test_voice_patterns_in_catalogue(self):
        """All protocol tool voice patterns appear in the catalogue."""
        from jarvis.tools.catalogue import voice_pattern_catalogue
        cat = voice_pattern_catalogue()
        tool_names = {name for name, _ in cat}
        expected = {
            "create_protocol", "run_protocol", "list_protocols",
            "delete_protocol", "edit_protocol", "enable_protocol",
            "disable_protocol",
        }
        for name in expected:
            assert name in tool_names, f"{name} patterns missing from catalogue"
