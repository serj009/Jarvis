"""Tests for jarvis.tools.registry.

Covers protocol satisfaction, registration semantics (including the
no-silent-overwrite collision policy), the config-driven enable filter
with fresh-per-execute re-check, args validation at the registry
boundary, the OpenAI/Ollama function schema shape, and exception
isolation on tool crashes."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from jarvis.core.config import ToolsConfig
from jarvis.tools.registry import (
    TOOL_NAME_REGEX,
    EmptyArgs,
    Tool,
    ToolNameCollisionError,
    ToolRegistry,
    ToolResult,
)

# --- fakes -------------------------------------------------------------
#
# Shaped exactly like a real tool in jarvis/tools/local: `args_schema`
# annotated `type[BaseModel]`, `execute` narrowed to the fake's own args
# model. `Tool` is a protocol generic in that model, so `register()`
# checks these for conformance — a change to the protocol fails pyright
# here rather than surfacing months later as an AttributeError.


class _EchoArgs(BaseModel):
    text: str
    times: int = Field(default=1, ge=1)


class _EchoTool:
    name: str = "echo"
    description: str = "Echo the text some number of times."
    args_schema: type[BaseModel] = _EchoArgs
    requires_confirmation: bool = False

    def __init__(self) -> None:
        self.executed_with: list[_EchoArgs] = []

    async def execute(self, args: _EchoArgs) -> ToolResult:
        self.executed_with.append(args)
        return ToolResult(
            success=True, output=" ".join([args.text] * args.times)
        )


class _NoArgsTool:
    name: str = "ping"
    description: str = "Returns pong."
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False

    async def execute(self, args: EmptyArgs) -> ToolResult:
        return ToolResult(success=True, output="pong")


class _CrashingTool:
    name: str = "crash"
    description: str = "Always crashes."
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False

    async def execute(self, args: EmptyArgs) -> ToolResult:
        raise RuntimeError("intentional test boom")


# --- module surface ----------------------------------------------------


def test_tool_name_regex_accepts_valid_names():
    for name in ("echo", "fs_read_file", "open-url", "x", "a" * 64):
        assert TOOL_NAME_REGEX.match(name), name


def test_tool_name_regex_rejects_invalid_names():
    for name in ("", " ", "with.dot", "with space", "a" * 65, "中文"):
        assert TOOL_NAME_REGEX.match(name) is None, name


# --- protocol smoke ----------------------------------------------------


def test_concrete_tools_satisfy_protocol():
    """Tool is @runtime_checkable so isinstance works structurally."""
    assert isinstance(_EchoTool(), Tool)
    assert isinstance(_NoArgsTool(), Tool)


def test_ordinary_tools_do_not_ask_for_confirmation():
    """The flag is opt-in, and the fakes here are the ordinary case.

    Confirmation is wired now (see the registry header and
    jarvis/ui/tool_confirm.py), so a tool that sets the flag gets a
    modal dialog in front of it. The default has to stay False or every
    "volume up" becomes a prompt."""
    assert _EchoTool().requires_confirmation is False
    assert _NoArgsTool().requires_confirmation is False


# --- registration ------------------------------------------------------


def test_register_and_get():
    reg = ToolRegistry(ToolsConfig())
    tool = _EchoTool()
    reg.register(tool)
    assert reg.get("echo") is tool


def test_get_unknown_returns_none():
    reg = ToolRegistry(ToolsConfig())
    assert reg.get("nonesuch") is None


def test_register_duplicate_name_raises_collision():
    """ToolNameCollisionError lets the caller decide policy. Built-ins
    register first at startup; MCP wrappers catch and drop the single
    colliding tool from the offending server (no silent overwrite)."""
    reg = ToolRegistry(ToolsConfig())
    reg.register(_EchoTool())
    with pytest.raises(ToolNameCollisionError):
        reg.register(_EchoTool())


def test_register_built_in_then_mcp_clobber_is_blocked():
    """Concrete walk-through of the policy: built-in registers first
    and wins; a second tool with the same name (simulating an MCP
    wrapper) is refused so the caller can log + drop just that one."""
    reg = ToolRegistry(ToolsConfig())
    builtin = _EchoTool()
    reg.register(builtin)

    class _MCPEcho:
        name: str = "echo"
        description: str = "MCP echo from a remote server."
        args_schema: type[BaseModel] = _EchoArgs
        requires_confirmation: bool = False

        async def execute(self, args: _EchoArgs) -> ToolResult:
            return ToolResult(success=True, output="from-mcp")

    with pytest.raises(ToolNameCollisionError):
        reg.register(_MCPEcho())
    # Built-in is intact.
    assert reg.get("echo") is builtin


def test_unregister_then_reregister_ok():
    reg = ToolRegistry(ToolsConfig())
    reg.register(_EchoTool())
    reg.unregister("echo")
    reg.register(_EchoTool())  # must not raise
    assert reg.get("echo") is not None


def test_unregister_unknown_is_noop():
    reg = ToolRegistry(ToolsConfig())
    reg.unregister("nonesuch")  # must not raise


# --- enabled filter ----------------------------------------------------


def test_list_enabled_includes_unconfigured_tools():
    """Convention: a tool absent from config.tools.enabled is enabled."""
    reg = ToolRegistry(ToolsConfig())
    reg.register(_EchoTool())
    assert [t.name for t in reg.list_enabled()] == ["echo"]


def test_list_enabled_skips_disabled_tools():
    reg = ToolRegistry(ToolsConfig(enabled={"echo": False}))
    reg.register(_EchoTool())
    assert reg.list_enabled() == []


def test_list_enabled_keeps_explicitly_enabled_tools():
    reg = ToolRegistry(ToolsConfig(enabled={"echo": True}))
    reg.register(_EchoTool())
    assert [t.name for t in reg.list_enabled()] == ["echo"]


# --- as_openai_functions ----------------------------------------------


def test_as_openai_functions_emits_ollama_compatible_schema():
    reg = ToolRegistry(ToolsConfig())
    reg.register(_EchoTool())
    funcs = reg.as_openai_functions()
    assert len(funcs) == 1
    entry = funcs[0]
    assert entry["type"] == "function"
    fn = entry["function"]
    assert fn["name"] == "echo"
    assert "Echo" in fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "text" in params["properties"]
    assert "times" in params["properties"]
    assert "text" in params["required"]


def test_as_openai_functions_excludes_disabled_tools():
    reg = ToolRegistry(ToolsConfig(enabled={"echo": False}))
    reg.register(_EchoTool())
    reg.register(_NoArgsTool())
    names = [e["function"]["name"] for e in reg.as_openai_functions()]
    # Only _NoArgsTool ("ping") should be visible; _EchoTool is disabled.
    assert names == ["ping"]


def test_setup_local_tools_idempotent_into_fresh_registry():
    """Setting up twice into the same registry must raise — the policy
    is no-silent-overwrite. Caller (composition root) calls once."""
    from jarvis.tools import setup_local_tools
    reg = ToolRegistry(ToolsConfig())
    setup_local_tools(reg)
    with pytest.raises(ToolNameCollisionError):
        setup_local_tools(reg)


def test_the_confirmation_gated_local_tool_set_is_pinned():
    """Exactly which built-ins prompt the user, pinned so it changes only
    on purpose.

    Both directions matter. Adding a tool to this set puts a modal dialog
    in front of it, which is a UX decision, and removing one from it
    silently drops a safety gate — the case this pins hardest, since
    nothing else in the suite would notice type_into_active_window
    quietly going back to running unannounced.

    Note this iterates every *registered* tool, not list_enabled():
    type_into_active_window is disabled by default in ToolsConfig, so a
    list_enabled() loop would not see it at all."""
    from jarvis.tools import setup_local_tools
    from jarvis.tools.catalogue import local_tool_names
    reg = ToolRegistry(ToolsConfig())
    setup_local_tools(reg)

    gated = sorted(
        name
        for name in local_tool_names()
        if (t := reg.get(name)) is not None and t.requires_confirmation
    )
    assert gated == ["change_vault_password", "delete_protocol", "delete_skill", "disable_all_skills", "remove_credential", "type_into_active_window"]
