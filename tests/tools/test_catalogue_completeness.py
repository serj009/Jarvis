"""Every registered tool with voice patterns is in the router's catalogue.

The router builds its pattern table from jarvis.tools.catalogue, not from
the live registry. get_time and switch_language were registered by
setup_local_tools but missing from the catalogue, so "который час" and
"говори по-украински" never matched their patterns and went to the model
(found while assembling the regression phrases, M3).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jarvis.core.config import JarvisConfig, ToolsConfig
from jarvis.tools import ToolRegistry, setup_local_tools
from jarvis.tools.catalogue import local_tool_names

# Registered tools deliberately kept out of the catalogue, with the reason.
_NOT_CATALOGUED: dict[str, str] = {
    "learn_app_alias": (
        "its 'запомни, что X это Y' patterns would also catch facts that are "
        "not app aliases; needs a decision (roadmap B-P3-6)"
    ),
    "remember_fact": "patterns pass no arguments (roadmap B-P3-6)",
    "search_memory": "patterns pass no arguments (roadmap B-P3-6)",
    "forget_fact": "patterns pass no arguments (roadmap B-P3-6)",
    "memory_status": "no voice patterns; listed with the memory tools (B-P3-6)",
}


def _registered_tools() -> dict[str, object]:
    registry = ToolRegistry(ToolsConfig())
    setup_local_tools(
        registry, JarvisConfig(),
        ollama_client=MagicMock(), memory_store=MagicMock(), stt=MagicMock(),
        tts_manager=MagicMock(), reminder_store=MagicMock(), game_mode=MagicMock(),
        announce=lambda text: None, on_timer_done=lambda name: None,
    )
    return dict(registry._tools)


def test_voice_routable_tools_are_in_the_catalogue():
    catalogued = set(local_tool_names())
    missing = sorted(
        name for name, tool in _registered_tools().items()
        if getattr(tool, "voice_patterns", ())
        and name not in catalogued and name not in _NOT_CATALOGUED
    )
    assert missing == [], (
        f"registered with voice patterns but not in jarvis/tools/catalogue.py: {missing}"
    )


def test_exclusions_are_still_needed():
    catalogued = set(local_tool_names())
    registered = _registered_tools()
    stale = sorted(n for n in _NOT_CATALOGUED if n in catalogued or n not in registered)
    assert stale == [], f"remove from _NOT_CATALOGUED: {stale}"
