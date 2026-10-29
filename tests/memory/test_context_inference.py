"""Tests for jarvis.memory.context_inference (T4.6 contextual inference)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.memory.context_inference import ContextInference
from jarvis.memory.store import MemoryStore
from jarvis.memory.tool_examples import ToolExampleStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def memory(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "ci_memory.db")
    await s.open()
    await s.add("User is playing Mafia 2 on Steam", category="games", tags=["game", "steam"])
    await s.add("User opened reddit.com/r/python", category="general", tags=["url"])
    yield s
    await s.close()


@pytest.fixture()
def tool_store(tmp_path) -> ToolExampleStore:
    ts = ToolExampleStore(tmp_path / "tool_examples.json")
    now = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    ts._data = {
        "launch_steam_game": [
            {"utterance": "запусти мафию", "args": {"game": "Mafia 2"},
             "count": 5, "last_used": now},
            {"utterance": "launch gta", "args": {"game": "GTA V"},
             "count": 2, "last_used": old},
        ],
        "open_url": [
            {"utterance": "open reddit", "args": {"url": "https://reddit.com"},
             "count": 8, "last_used": now},
        ],
        "open_app": [
            {"utterance": "open chrome", "args": {"app_name": "chrome"},
             "count": 20, "last_used": now},
        ],
    }
    ts._save()
    return ts


@pytest.fixture()
def ci(memory, tool_store) -> ContextInference:
    return ContextInference(memory, tool_store)


@pytest.fixture()
def ci_no_tools(memory) -> ContextInference:
    return ContextInference(memory, tool_example_store=None)


# ---------------------------------------------------------------------------
# get_recent_tool_activity
# ---------------------------------------------------------------------------

class TestRecentToolActivity:
    def test_returns_recent_calls(self, ci) -> None:
        recent = ci.get_recent_tool_activity("launch_steam_game", days=7)
        # "запусти мафию" is recent, "launch gta" is 60 days old
        assert len(recent) >= 1
        assert recent[0]["args"]["game"] == "Mafia 2"

    def test_filters_old_calls(self, ci) -> None:
        recent = ci.get_recent_tool_activity("launch_steam_game", days=7)
        # Only the recent one should be included
        games = [r["args"]["game"] for r in recent]
        assert "GTA V" not in games

    def test_returns_empty_for_unknown_tool(self, ci) -> None:
        assert ci.get_recent_tool_activity("nonexistent_tool") == []

    def test_returns_empty_when_no_tool_store(self, ci_no_tools) -> None:
        assert ci_no_tools.get_recent_tool_activity("open_app") == []

    def test_sorted_by_count(self, ci) -> None:
        # All recent calls for open_url
        recent = ci.get_recent_tool_activity("open_url", days=30)
        if len(recent) > 1:
            assert recent[0]["count"] >= recent[1]["count"]


# ---------------------------------------------------------------------------
# infer_implicit_reference
# ---------------------------------------------------------------------------

class TestImplicitReference:
    @pytest.mark.asyncio()
    async def test_resolves_game_reference_ru(self, ci) -> None:
        result = await ci.infer_implicit_reference("запусти ту игру")
        assert result is not None
        assert "Mafia 2" in result
        assert "(inferred:" in result

    @pytest.mark.asyncio()
    async def test_resolves_game_reference_en(self, ci) -> None:
        result = await ci.infer_implicit_reference("launch that game")
        assert result is not None
        assert "Mafia 2" in result

    @pytest.mark.asyncio()
    async def test_resolves_site_reference(self, ci) -> None:
        result = await ci.infer_implicit_reference("open that site")
        assert result is not None
        assert "reddit" in result.lower()

    @pytest.mark.asyncio()
    async def test_resolves_app_reference(self, ci) -> None:
        result = await ci.infer_implicit_reference("open that app")
        assert result is not None
        assert "chrome" in result.lower()

    @pytest.mark.asyncio()
    async def test_returns_none_for_no_match(self, ci) -> None:
        result = await ci.infer_implicit_reference("what is the weather like")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_empty_utterance(self, ci) -> None:
        result = await ci.infer_implicit_reference("")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_whitespace(self, ci) -> None:
        result = await ci.infer_implicit_reference("   ")
        assert result is None

    @pytest.mark.asyncio()
    async def test_fallback_to_memory_when_no_tools(self, ci_no_tools) -> None:
        # Without tool store, should try memory search
        result = await ci_no_tools.infer_implicit_reference("запусти ту игру")
        # May or may not find something — but should not crash
        # If memory has game facts, it might resolve
        assert result is None or "inferred" in result

    @pytest.mark.asyncio()
    async def test_preserves_original_utterance(self, ci) -> None:
        original = "запусти ту игру пожалуйста"
        result = await ci.infer_implicit_reference(original)
        if result:
            assert result.startswith(original)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio()
    async def test_tool_store_with_empty_args(self, memory, tmp_path) -> None:
        ts = ToolExampleStore(tmp_path / "empty_args.json")
        ts._data = {
            "launch_steam_game": [
                {"utterance": "play", "args": {}, "count": 1,
                 "last_used": datetime.now(UTC).isoformat()},
            ],
        }
        ts._save()
        ci = ContextInference(memory, ts)
        # Should not crash, just fall back to memory
        result = await ci.infer_implicit_reference("запусти ту игру")
        # Either None or memory-based result
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio()
    async def test_closed_memory_store(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "closed.db")
        # Not opened — should handle gracefully
        ci = ContextInference(s)
        result = await ci.infer_implicit_reference("открой тот сайт")
        assert result is None
