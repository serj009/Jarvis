"""Tests for jarvis.memory.auto_recall (T4.4 auto-recall injection)."""

from __future__ import annotations

import pytest

from jarvis.memory.auto_recall import get_relevant_memories
from jarvis.memory.store import MemoryStore


@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    """Create a temporary MemoryStore seeded with a few facts."""
    s = MemoryStore(db_path=tmp_path / "test_memory.db")
    await s.open()
    await s.add("User's name is Serhii", category="personal", tags=["name"])
    await s.add("User likes Stalker 2", category="games", tags=["game"])
    await s.add("User lives in Kyiv", category="personal", tags=["location"])
    await s.add("User works as a software engineer", category="work", tags=["profession"])
    yield s
    await s.close()


class TestGetRelevantMemories:
    @pytest.mark.asyncio()
    async def test_returns_none_for_empty_store(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "empty.db")
        await s.open()
        result = await get_relevant_memories(s, "hello")
        assert result is None
        await s.close()

    @pytest.mark.asyncio()
    async def test_returns_none_for_empty_utterance(self, store) -> None:
        result = await get_relevant_memories(store, "")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_whitespace_utterance(self, store) -> None:
        result = await get_relevant_memories(store, "   ")
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_when_store_not_open(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "closed.db")
        result = await get_relevant_memories(s, "hello")
        assert result is None

    @pytest.mark.asyncio()
    async def test_finds_relevant_memory_by_keyword(self, store) -> None:
        result = await get_relevant_memories(store, "Stalker")
        assert result is not None
        assert "Stalker" in result
        assert "[Relevant memories" in result

    @pytest.mark.asyncio()
    async def test_finds_name_fact(self, store) -> None:
        result = await get_relevant_memories(store, "Serhii")
        assert result is not None
        assert "Serhii" in result

    @pytest.mark.asyncio()
    async def test_returns_none_for_unrelated_query(self, store) -> None:
        # "quantum physics" should not match any stored fact
        result = await get_relevant_memories(
            store, "quantum physics",
            min_score=0.5,
        )
        assert result is None

    @pytest.mark.asyncio()
    async def test_respects_max_results(self, store) -> None:
        result = await get_relevant_memories(
            store, "User",
            max_results=2,
            min_score=0.0,  # accept everything
        )
        assert result is not None
        # Count bullet points (lines starting with "- ")
        bullet_lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(bullet_lines) <= 2

    @pytest.mark.asyncio()
    async def test_format_has_header_and_bullets(self, store) -> None:
        result = await get_relevant_memories(
            store, "Serhii",
            min_score=0.0,
        )
        assert result is not None
        lines = result.split("\n")
        assert lines[0] == "[Relevant memories from past conversations:]"
        assert all(l.startswith("- ") for l in lines[1:])

    @pytest.mark.asyncio()
    async def test_high_min_score_filters_weak_matches(self, store) -> None:
        result = await get_relevant_memories(
            store, "something vaguely related",
            min_score=0.99,
        )
        # With min_score=0.99, almost nothing should pass
        assert result is None
