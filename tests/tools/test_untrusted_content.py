"""Tests for Task 10A untrusted-content classification.

What is pinned here:
  - returns_untrusted_content_for_tool() lookup order
    (explicit attribute > static set > default False),
  - the exact set of built-in tools flagged untrusted (a guard: adding a
    tool that SHOULD be untrusted but isn't listed fails the pin; a new
    trusted/local tool just stays False, the conscious default),
  - every UNTRUSTED_CONTENT_TOOLS name is a real catalogue tool (no dead
    entries),
  - every catalogue tool resolves to an explicit bool,
  - BaseIntegration defaults to untrusted (True).
"""

from __future__ import annotations

import pytest

from jarvis.tools.registry import EmptyArgs, ToolResult
from jarvis.tools.risk import (
    UNTRUSTED_CONTENT_TOOLS,
    returns_untrusted_content_for_tool,
)

# The exact built-in untrusted set, pinned. Changing tool classification is
# a deliberate security decision — update this list in the same change.
_PINNED_UNTRUSTED = {
    # web / external API text
    "content_reader", "save_page_knowledge", "price_lookup", "youtube_summary",
    "compare_pages", "translate_page", "scrape_images", "get_latest_news",
    "research", "read_more", "copy_research", "get_weather", "download_file",
    "check_page_changes", "deep_research",
    # OCR / screen
    "read_screen_text", "see_screen", "watch_screen", "screenshot",
    "full_page_screenshot",
    # browser tabs
    "browser_tabs", "find_tab", "count_tabs",
    # clipboard / filesystem / windows
    "clipboard", "show_clipboard_history", "paste_clipboard_item",
    "list_directory", "file_manager", "list_windows",
    # git / dev
    "git_tool", "coding_agent",
    # bookmarks / rss / page monitors
    "bookmarks", "list_rss_feeds", "list_page_monitors",
    "start_page_monitor", "stop_page_monitor",
    # game-save discovery
    "find_game_saves", "list_known_games",
    # notes
    "read_note",
}


def test_pinned_untrusted_set_matches_source() -> None:
    """The source set equals the pin. Adding/removing a tool from the
    untrusted set must be a conscious edit here too."""
    assert set(UNTRUSTED_CONTENT_TOOLS) == _PINNED_UNTRUSTED


# --- resolver lookup order --------------------------------------------------


class _ExplicitTrue:
    name = "whatever_local"  # not in the set
    description = "t"
    args_schema = EmptyArgs
    requires_confirmation = False
    returns_untrusted_content = True

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _ExplicitFalseButInSet:
    name = "content_reader"  # IS in the set, but attribute overrides
    description = "t"
    args_schema = EmptyArgs
    requires_confirmation = False
    returns_untrusted_content = False

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _InSetNoAttr:
    name = "content_reader"
    description = "t"
    args_schema = EmptyArgs
    requires_confirmation = False

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


class _TrustedLocalNoAttr:
    name = "calculator"
    description = "t"
    args_schema = EmptyArgs
    requires_confirmation = False

    async def execute(self, args):
        return ToolResult(success=True, output="ok")


def test_explicit_attribute_true_wins() -> None:
    assert returns_untrusted_content_for_tool(_ExplicitTrue()) is True


def test_explicit_attribute_false_overrides_set() -> None:
    # The attribute is authoritative — a plugin/tool can opt out even if its
    # name is in the set.
    assert returns_untrusted_content_for_tool(_ExplicitFalseButInSet()) is False


def test_static_set_used_when_no_attribute() -> None:
    assert returns_untrusted_content_for_tool(_InSetNoAttr()) is True


def test_trusted_local_defaults_false() -> None:
    assert returns_untrusted_content_for_tool(_TrustedLocalNoAttr()) is False


def test_unknown_tool_defaults_false() -> None:
    class _Unknown:
        name = "totally_unknown_tool_xyz"
        requires_confirmation = False
    assert returns_untrusted_content_for_tool(_Unknown()) is False


# --- catalogue coverage (guard) ---------------------------------------------


def test_untrusted_set_has_no_dead_entries() -> None:
    """Every name in UNTRUSTED_CONTENT_TOOLS is a real catalogue tool."""
    try:
        from jarvis.tools.catalogue import local_tool_names
        names = set(local_tool_names())
    except ImportError:
        pytest.skip("catalogue not importable")
        return
    dead = sorted(UNTRUSTED_CONTENT_TOOLS - names)
    assert not dead, f"UNTRUSTED_CONTENT_TOOLS has names not in catalogue: {dead}"


def test_every_catalogue_tool_resolves_to_explicit_bool() -> None:
    """Every registered tool yields a concrete bool from the resolver —
    there is no None / unclassified state for a built-in."""
    try:
        from jarvis.core.config import ToolsConfig
        from jarvis.tools import setup_local_tools
        from jarvis.tools.catalogue import local_tool_names
        from jarvis.tools.registry import ToolRegistry
    except ImportError:
        pytest.skip("tools not importable")
        return
    reg = ToolRegistry(ToolsConfig())
    setup_local_tools(reg)
    for name in local_tool_names():
        tool = reg.get(name)
        if tool is None:
            continue
        val = returns_untrusted_content_for_tool(tool)
        assert isinstance(val, bool), f"{name} did not resolve to a bool"


# --- integration default ----------------------------------------------------


def test_base_integration_defaults_to_untrusted() -> None:
    from jarvis.integrations.base import BaseIntegration

    assert BaseIntegration.returns_untrusted_content is True
