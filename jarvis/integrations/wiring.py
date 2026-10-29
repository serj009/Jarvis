"""Runtime wiring for integrations — Layer 3.

Turns an enabled plugin's tool schemas into live ToolRegistry tools (the
same executable path MCP-adapted tools use), and adds/removes them as the
user toggles integrations. Also exposes the plugins' proactive data to the
ProactiveEngine.

Why registry tools (not voice patterns): the router's pattern table is
assembled once at construction and is first-match-wins by design. Injecting
patterns at runtime would mean rebuilding that table live — invasive and
risky. Registry tools, by contrast, are looked up per call and can be
registered/unregistered freely, exactly like MCP tools. So an enabled
integration's capabilities reach the model through LLM tool-calling, which
is the right surface for open-ended online actions anyway.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, create_model

from jarvis.tools.registry import ToolResult
from jarvis.tools.risk import RiskLevel

log = logging.getLogger(__name__)


def _schema_to_args_model(name: str, parameters: dict) -> type[BaseModel]:
    """Build a permissive pydantic model from a JSON-schema parameters dict.

    Integration tool schemas are provider-shaped (OpenAI function-calling).
    We don't need strict validation here — the plugin re-validates — so we
    map each declared property to an optional field and allow extras.
    """
    props = (parameters or {}).get("properties", {}) or {}
    required = set((parameters or {}).get("required", []) or [])
    type_map = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }
    fields: dict[str, tuple] = {}
    for field_name, spec in props.items():
        py_type = type_map.get((spec or {}).get("type", "string"), str)
        if field_name in required:
            fields[field_name] = (py_type, ...)
        else:
            fields[field_name] = (py_type | None, None)
    model = create_model(  # type: ignore[call-overload]
        f"{name}_Args",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )
    return model


class IntegrationToolAdapter:
    """Adapts one plugin tool schema to the ToolRegistry Tool protocol."""

    requires_confirmation: bool = False
    # Online actions touch the network and remote state → treat as WRITE.
    risk_level = RiskLevel.WRITE

    def __init__(self, plugin, schema: dict) -> None:
        fn = schema.get("function", {})
        self.name: str = fn.get("name", "")
        self.description: str = fn.get("description", "")
        self._plugin = plugin
        self._args_model = _schema_to_args_model(
            self.name or "integration", fn.get("parameters", {})
        )

    @property
    def args_schema(self) -> type[BaseModel]:
        return self._args_model

    async def execute(self, args: BaseModel) -> ToolResult:
        try:
            raw = args.model_dump(exclude_none=True)
        except Exception:
            raw = {}
        try:
            result = await self._plugin.execute_tool(self.name, raw)
        except Exception as e:
            log.warning("[integrations] tool %s failed", self.name, exc_info=True)
            return ToolResult(success=False, error=f"{self.name} failed: {e}")
        return ToolResult(success=True, output=str(result))


def wire_integration_tools(tool_registry, integration_registry) -> dict[str, list[str]]:
    """Sync the live ToolRegistry with the enabled integrations' tools.

    Registers adapters for every enabled plugin's tools that aren't already
    present, and unregisters adapters whose plugin is no longer enabled.
    Returns {"added": [...], "removed": [...]} for logging/tests.

    Idempotent: safe to call on every enable/disable toggle.
    """
    added: list[str] = []
    removed: list[str] = []

    # Names the enabled plugins currently want registered.
    wanted: dict[str, tuple] = {}  # name -> (plugin, schema)
    for plugin in integration_registry.get_enabled():
        try:
            for schema in plugin.get_tools():
                tname = schema.get("function", {}).get("name")
                if tname:
                    wanted[tname] = (plugin, schema)
        except Exception:
            log.warning("[integrations] get_tools failed for %s",
                        getattr(plugin, "name", "?"), exc_info=True)

    # Track which registry tools are integration-owned so we can remove
    # the stale ones without touching local/MCP tools.
    owned: set[str] = getattr(tool_registry, "_integration_tool_names", set())

    # Add newly-wanted tools.
    for tname, (plugin, schema) in wanted.items():
        if tname in owned and tool_registry.get(tname) is not None:
            continue
        existing = tool_registry.get(tname)
        if existing is not None and tname not in owned:
            # A local/MCP tool already owns this name — never clobber it.
            log.warning(
                "[integrations] tool name %r already registered; skipping", tname
            )
            continue
        try:
            tool_registry.register(IntegrationToolAdapter(plugin, schema))
            owned.add(tname)
            added.append(tname)
        except Exception:
            log.warning("[integrations] could not register %r", tname, exc_info=True)

    # Remove integration tools no longer wanted.
    for tname in list(owned):
        if tname not in wanted:
            tool_registry.unregister(tname)
            owned.discard(tname)
            removed.append(tname)

    tool_registry._integration_tool_names = owned  # type: ignore[attr-defined]
    if added or removed:
        log.info("[integrations] tools synced: +%s -%s", added, removed)
    return {"added": added, "removed": removed}


def collect_integration_events(integration_registry) -> list[dict]:
    """Gather proactive data (birthdays/events) from enabled integrations,
    shaped for ProactiveScenarios.upcoming_date (needs name/date/days_until).
    """
    return integration_registry.get_all_proactive_data()
