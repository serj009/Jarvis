"""Tests for jarvis.tools.tool_filter — dynamic keyword-based tool filtering.

What is pinned here:
  - Each tool category is activated by its declared keywords (EN/RU/UK).
  - ALWAYS_INCLUDED tools appear in every filtered result.
  - An unknown/unmatched utterance returns ALL tools (safe fallback).
  - Mixed-language queries activate the union of matched categories.
  - A too-aggressive filter (< 3 results) falls back to all tools.
  - Empty schemas in → empty schemas out (no crash).
  - MAX_FILTERED_TOOLS cap is respected.
"""

from __future__ import annotations

import pytest

from jarvis.tools.tool_filter import (
    ALWAYS_INCLUDED,
    MAX_FILTERED_TOOLS,
    TOOL_CATEGORIES,
    filter_tools_for_utterance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema(name: str) -> dict:
    """Minimal OpenAI-format tool schema stub."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Stub for {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _all_tool_names_from_categories() -> list[str]:
    """Collect every unique tool name across all categories + ALWAYS_INCLUDED."""
    names: set[str] = set(ALWAYS_INCLUDED)
    for cat in TOOL_CATEGORIES.values():
        names.update(cat["tools"])
    return sorted(names)


# A realistic "full" schema set: one entry per declared tool name.
_ALL_SCHEMAS = [_make_schema(n) for n in _all_tool_names_from_categories()]

# A few extra schemas that represent MCP / dynamic tools not in any category.
_EXTRA_MCP = [_make_schema("mcp_trayce_search"), _make_schema("mcp_notion_read")]
_ALL_SCHEMAS_WITH_MCP = _ALL_SCHEMAS + _EXTRA_MCP


def _names(schemas: list[dict]) -> set[str]:
    """Extract tool names from a list of schemas."""
    return {s["function"]["name"] for s in schemas}


# ---------------------------------------------------------------------------
# Category activation — English
# ---------------------------------------------------------------------------

class TestEnglishKeywords:
    """English keyword matching activates the right categories."""

    def test_music_query_includes_media_tools(self):
        result = filter_tools_for_utterance("play some music", _ALL_SCHEMAS)
        names = _names(result)
        assert "play_youtube_music" in names
        assert "volume" in names
        assert "launch_steam_game" in names
        # Should NOT include unrelated tools.
        assert "deep_research" not in names
        assert "lock_screen" not in names

    def test_app_query_includes_app_tools(self):
        result = filter_tools_for_utterance("open spotify", _ALL_SCHEMAS)
        names = _names(result)
        assert "open_app" in names
        assert "close_app" in names
        assert "open_url" in names
        assert "launch_workspace" in names

    def test_weather_query_includes_info_tools(self):
        result = filter_tools_for_utterance(
            "what's the weather like today", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "get_weather" in names
        assert "get_time" in names
        assert "screenshot" in names
        # "today" is an info keyword — should activate info.
        assert "report_cpu_and_memory_percentages" in names

    def test_note_query_includes_note_tools(self):
        result = filter_tools_for_utterance(
            "take a note: buy milk", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "take_note" in names
        assert "read_note" in names
        assert "append_to_note" in names

    def test_research_query_includes_research_tools(self):
        result = filter_tools_for_utterance(
            "research quantum computing", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "research" in names
        assert "deep_research" in names
        assert "close_research" in names

    def test_dashboard_query_includes_ui_panel_tools(self):
        result = filter_tools_for_utterance("show dashboard", _ALL_SCHEMAS)
        names = _names(result)
        assert "show_dashboard" in names
        assert "close_dashboard" in names

    def test_clipboard_query_includes_ui_and_system_tools(self):
        result = filter_tools_for_utterance(
            "show clipboard history", _ALL_SCHEMAS,
        )
        names = _names(result)
        # "clipboard" keyword hits both ui_panels and system.
        assert "show_clipboard_history" in names
        assert "clipboard" in names

    def test_lock_query_includes_system_tools(self):
        result = filter_tools_for_utterance("lock the screen", _ALL_SCHEMAS)
        names = _names(result)
        assert "lock_screen" in names
        assert "type_into_active_window" in names


# ---------------------------------------------------------------------------
# Category activation — Russian
# ---------------------------------------------------------------------------

class TestRussianKeywords:
    """Russian keyword stems activate the right categories."""

    def test_music_ru(self):
        result = filter_tools_for_utterance(
            "включи музыку", _ALL_SCHEMAS,
        )
        names = _names(result)
        # "музык" is a media keyword, "включи" is an apps keyword.
        assert "play_youtube_music" in names
        assert "open_app" in names

    def test_weather_ru(self):
        result = filter_tools_for_utterance("какая погода", _ALL_SCHEMAS)
        names = _names(result)
        assert "get_weather" in names

    def test_notes_ru(self):
        result = filter_tools_for_utterance(
            "запиши заметку: купить молоко", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "take_note" in names

    def test_research_ru(self):
        result = filter_tools_for_utterance(
            "исследуй тему искусственного интеллекта", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "research" in names
        assert "deep_research" in names

    def test_system_ru(self):
        result = filter_tools_for_utterance(
            "заблокируй экран", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "lock_screen" in names


# ---------------------------------------------------------------------------
# Category activation — Ukrainian
# ---------------------------------------------------------------------------

class TestUkrainianKeywords:
    """Ukrainian keyword stems activate the right categories."""

    def test_music_uk(self):
        result = filter_tools_for_utterance(
            "увімкни музику", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "play_youtube_music" in names
        assert "open_app" in names

    def test_weather_uk(self):
        result = filter_tools_for_utterance("яка погода", _ALL_SCHEMAS)
        names = _names(result)
        assert "get_weather" in names

    def test_notes_uk(self):
        result = filter_tools_for_utterance(
            "запиши нотатку", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "take_note" in names

    def test_system_uk(self):
        result = filter_tools_for_utterance("заблокуй екран", _ALL_SCHEMAS)
        names = _names(result)
        assert "lock_screen" in names


# ---------------------------------------------------------------------------
# Mixed language
# ---------------------------------------------------------------------------

class TestMixedLanguage:
    """Queries mixing languages activate the union of matched categories."""

    def test_mixed_ru_en(self):
        # "открой" (RU apps) + "dashboard" (EN ui_panels)
        result = filter_tools_for_utterance(
            "открой dashboard", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "open_app" in names      # apps category
        assert "show_dashboard" in names  # ui_panels category

    def test_mixed_uk_en(self):
        # "відкрий" (UK apps) + "music" (EN media)
        result = filter_tools_for_utterance(
            "відкрий music player", _ALL_SCHEMAS,
        )
        names = _names(result)
        assert "open_app" in names           # apps
        assert "play_youtube_music" in names  # media


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------

class TestFallback:
    """Unknown queries return the full tool set (safe fallback)."""

    def test_unknown_query_returns_all(self):
        result = filter_tools_for_utterance(
            "tell me a joke about penguins", _ALL_SCHEMAS,
        )
        assert result is _ALL_SCHEMAS  # same list object (identity check)

    def test_gibberish_returns_all(self):
        result = filter_tools_for_utterance(
            "xkcd 927 zxcvbn", _ALL_SCHEMAS,
        )
        assert result is _ALL_SCHEMAS

    def test_empty_utterance_returns_all(self):
        result = filter_tools_for_utterance("", _ALL_SCHEMAS)
        assert result is _ALL_SCHEMAS


# ---------------------------------------------------------------------------
# ALWAYS_INCLUDED
# ---------------------------------------------------------------------------

class TestAlwaysIncluded:
    """ALWAYS_INCLUDED tools are present in every filtered result."""

    def test_always_included_in_media_query(self):
        result = filter_tools_for_utterance("play a game on steam", _ALL_SCHEMAS)
        names = _names(result)
        for tool_name in ALWAYS_INCLUDED:
            assert tool_name in names, (
                f"{tool_name} should always be included"
            )

    def test_always_included_in_research_query(self):
        result = filter_tools_for_utterance("deep research AI", _ALL_SCHEMAS)
        names = _names(result)
        for tool_name in ALWAYS_INCLUDED:
            assert tool_name in names

    def test_always_included_in_system_query(self):
        result = filter_tools_for_utterance("lock screen now", _ALL_SCHEMAS)
        names = _names(result)
        for tool_name in ALWAYS_INCLUDED:
            assert tool_name in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions and safety rails."""

    def test_empty_schemas_returns_empty(self):
        result = filter_tools_for_utterance("play music", [])
        assert result == []

    def test_too_few_matches_falls_back_to_all(self):
        """If only 1-2 tools match (e.g. the schema set is minimal),
        the safety rail returns all tools."""
        tiny_schemas = [_make_schema("get_time"), _make_schema("volume")]
        # "deep research" won't match either of those names.
        result = filter_tools_for_utterance("deep research AI", tiny_schemas)
        assert result is tiny_schemas

    def test_max_filtered_tools_cap(self):
        """Even if many categories match, the cap is respected."""
        # Build a giant schema set.
        giant = [_make_schema(f"tool_{i}") for i in range(50)]
        # Add schemas for known tool names so the filter has something to match.
        giant.extend(_ALL_SCHEMAS)
        result = filter_tools_for_utterance("play music", giant)
        assert len(result) <= MAX_FILTERED_TOOLS

    def test_case_insensitive(self):
        """Keywords match regardless of utterance case."""
        result = filter_tools_for_utterance("PLAY MUSIC", _ALL_SCHEMAS)
        names = _names(result)
        assert "play_youtube_music" in names

    def test_mcp_tools_excluded_when_category_matches(self):
        """MCP tools not in any category are excluded when a category
        matches (they would only appear in the fallback-all path)."""
        result = filter_tools_for_utterance(
            "play some music", _ALL_SCHEMAS_WITH_MCP,
        )
        names = _names(result)
        assert "mcp_trayce_search" not in names
        assert "mcp_notion_read" not in names

    def test_mcp_tools_included_in_fallback(self):
        """MCP tools are included in the fallback-all path."""
        result = filter_tools_for_utterance(
            "tell me a joke", _ALL_SCHEMAS_WITH_MCP,
        )
        names = _names(result)
        assert "mcp_trayce_search" in names
        assert "mcp_notion_read" in names


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

class TestStructuralInvariants:
    """Properties that must hold for any input."""

    def test_result_is_subset_of_input(self):
        """The filter never invents schemas; every result is from the input."""
        for utterance in [
            "play music", "open chrome", "what's the weather",
            "take a note", "deep research", "show logs",
        ]:
            result = filter_tools_for_utterance(utterance, _ALL_SCHEMAS)
            for schema in result:
                assert schema in _ALL_SCHEMAS

    def test_result_is_never_empty_when_input_nonempty(self):
        """The filter never returns an empty list for a non-empty input
        (either some tools match or the fallback returns all)."""
        for utterance in [
            "play music", "xyzzy", "", "open chrome",
            "заблокируй", "відкрий",
        ]:
            result = filter_tools_for_utterance(utterance, _ALL_SCHEMAS)
            assert len(result) > 0

    def test_filtered_result_smaller_than_all(self):
        """When a category matches, the result is strictly smaller than
        the full set (otherwise filtering is pointless)."""
        result = filter_tools_for_utterance(
            "lock the screen", _ALL_SCHEMAS,
        )
        assert len(result) < len(_ALL_SCHEMAS)

    def test_every_category_has_all_keyword_lists(self):
        """Every category declares EN, RU, and UK keyword lists."""
        for name, cat in TOOL_CATEGORIES.items():
            assert "tools" in cat, f"{name} missing 'tools'"
            assert "keywords_en" in cat, f"{name} missing 'keywords_en'"
            assert "keywords_ru" in cat, f"{name} missing 'keywords_ru'"
            assert "keywords_uk" in cat, f"{name} missing 'keywords_uk'"
            assert len(cat["tools"]) > 0, f"{name} has empty tools list"
            assert len(cat["keywords_en"]) > 0, f"{name} has no EN keywords"
            assert len(cat["keywords_ru"]) > 0, f"{name} has no RU keywords"
            assert len(cat["keywords_uk"]) > 0, f"{name} has no UK keywords"
