"""Tests for jarvis.integrations.wiring (Layer 3 runtime wiring)."""

from __future__ import annotations

from jarvis.core.config import ToolsConfig
from jarvis.integrations.registry import IntegrationRegistry
from jarvis.integrations.wiring import (
    IntegrationToolAdapter,
    _schema_to_args_model,
    collect_integration_events,
    wire_integration_tools,
)
from jarvis.tools.registry import ToolRegistry


def _reg_with_example(enabled=True):
    reg = IntegrationRegistry(enabled_names=["example"] if enabled else [])
    reg.discover_plugins()
    return reg


# ── schema → args model ─────────────────────────────────────────────

class TestSchemaModel:
    def test_builds_optional_fields(self):
        model = _schema_to_args_model("t", {
            "type": "object",
            "properties": {"q": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["q"],
        })
        # required present, optional defaulting
        inst = model(q="hi")
        assert inst.q == "hi"

    def test_allows_extra(self):
        model = _schema_to_args_model("t", {"type": "object", "properties": {}})
        inst = model(anything="ok")  # extra=allow
        assert inst.model_dump().get("anything") == "ok"


# ── adapter ──────────────────────────────────────────────────────────

class TestAdapter:
    async def test_adapter_executes_via_plugin(self):
        reg = _reg_with_example()
        plugin = reg.get("example")
        schema = plugin.get_tools()[0]
        adapter = IntegrationToolAdapter(plugin, schema)
        assert adapter.name == "example_ping"
        result = await adapter.execute(adapter.args_schema())
        assert result.success
        assert "pong" in result.output

    async def test_adapter_reports_plugin_error(self):
        reg = _reg_with_example()
        plugin = reg.get("example")
        schema = {"function": {"name": "example_ping", "description": "",
                               "parameters": {}}}
        adapter = IntegrationToolAdapter(plugin, schema)

        async def boom(name, args):
            raise RuntimeError("network down")
        plugin.execute_tool = boom
        result = await adapter.execute(adapter.args_schema())
        assert not result.success
        assert "network down" in result.error


# ── wire_integration_tools ───────────────────────────────────────────

class TestWiring:
    def test_registers_enabled_tools(self):
        tools = ToolRegistry(ToolsConfig())
        ireg = _reg_with_example(enabled=True)
        out = wire_integration_tools(tools, ireg)
        assert "example_ping" in out["added"]
        assert tools.get("example_ping") is not None

    def test_nothing_when_disabled(self):
        tools = ToolRegistry(ToolsConfig())
        ireg = _reg_with_example(enabled=False)
        out = wire_integration_tools(tools, ireg)
        assert out["added"] == []
        assert tools.get("example_ping") is None

    def test_unregisters_when_disabled_later(self):
        tools = ToolRegistry(ToolsConfig())
        ireg = _reg_with_example(enabled=True)
        wire_integration_tools(tools, ireg)
        assert tools.get("example_ping") is not None
        # Disable, re-sync → tool removed.
        ireg.disable("example")
        out = wire_integration_tools(tools, ireg)
        assert "example_ping" in out["removed"]
        assert tools.get("example_ping") is None

    def test_idempotent(self):
        tools = ToolRegistry(ToolsConfig())
        ireg = _reg_with_example(enabled=True)
        wire_integration_tools(tools, ireg)
        out2 = wire_integration_tools(tools, ireg)
        assert out2["added"] == [] and out2["removed"] == []

    def test_does_not_clobber_existing_local_tool(self):
        from jarvis.tools.registry import ToolResult
        from pydantic import BaseModel

        class _Local:
            name = "example_ping"
            description = "local"
            requires_confirmation = False
            @property
            def args_schema(self):
                return BaseModel
            async def execute(self, args):
                return ToolResult(success=True, output="local")

        tools = ToolRegistry(ToolsConfig())
        tools.register(_Local())
        ireg = _reg_with_example(enabled=True)
        wire_integration_tools(tools, ireg)
        # The local tool stays — integration did not overwrite it.
        assert tools.get("example_ping").description == "local"

    async def test_wired_tool_executes_through_registry(self):
        tools = ToolRegistry(ToolsConfig())
        ireg = _reg_with_example(enabled=True)
        wire_integration_tools(tools, ireg)
        result = await tools.execute("example_ping", {})
        assert result.success and "pong" in result.output


# ── events ───────────────────────────────────────────────────────────

def test_collect_events_empty_for_example():
    ireg = _reg_with_example(enabled=True)
    assert collect_integration_events(ireg) == []
