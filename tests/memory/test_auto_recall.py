"""Tests for jarvis.memory.auto_recall (T4.4 auto-recall + T4.6 tier-aware injection)."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.memory.auto_recall import get_relevant_memories, _text_for_injection
from jarvis.memory.store import Fact, MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_tier(db_path, fact_id: str, tier: str, condensed: str | None = None) -> None:
    """Directly set tier + condensed_text in the DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE facts SET tier = ?, condensed_text = ? WHERE id = ?",
        (tier, condensed, fact_id),
    )
    conn.commit()
    conn.close()


def _get_tier(db_path, fact_id: str) -> str:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT tier FROM facts WHERE id = ?", (fact_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Original tests (T4.4)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tier-aware injection (T4.6)
# ---------------------------------------------------------------------------

class TestTextForInjection:
    """Unit tests for _text_for_injection (tier-based text selection)."""

    def test_hot_uses_original(self) -> None:
        fact = Fact(id="1", text="Full original text", tier="hot")
        assert _text_for_injection(fact) == "Full original text"

    def test_warm_uses_condensed(self) -> None:
        fact = Fact(id="2", text="Full original text", tier="warm",
                    condensed_text="Full original...")
        assert _text_for_injection(fact) == "Full original..."

    def test_warm_fallback_to_original(self) -> None:
        fact = Fact(id="3", text="Full text", tier="warm", condensed_text=None)
        assert _text_for_injection(fact) == "Full text"

    def test_cold_uses_condensed_with_prefix(self) -> None:
        fact = Fact(id="4", text="Very old full text", tier="cold",
                    condensed_text="Very old...")
        result = _text_for_injection(fact)
        assert result == "[old memory] Very old..."

    def test_cold_fallback_to_original_with_prefix(self) -> None:
        fact = Fact(id="5", text="Old text", tier="cold", condensed_text=None)
        result = _text_for_injection(fact)
        assert result == "[old memory] Old text"

    def test_archive_returns_none(self) -> None:
        fact = Fact(id="6", text="Ancient text", tier="archive",
                    condensed_text="[tags] Anc...")
        assert _text_for_injection(fact) is None

    def test_default_tier_is_hot(self) -> None:
        fact = Fact(id="7", text="No tier set")
        assert _text_for_injection(fact) == "No tier set"


class TestTierAwareRecall:
    """Integration tests: auto-recall with different tiers."""

    @pytest.mark.asyncio()
    async def test_warm_facts_use_condensed_text(self, store) -> None:
        # Find the Stalker fact and make it warm with condensed text
        facts = await store.search("Stalker", limit=1)
        assert facts
        fid = facts[0].id
        _set_tier(store.db_path, fid, "warm", "User plays Stalker 2")

        result = await get_relevant_memories(store, "Stalker", min_score=0.0)
        assert result is not None
        assert "User plays Stalker 2" in result

    @pytest.mark.asyncio()
    async def test_cold_facts_get_old_memory_prefix(self, store) -> None:
        facts = await store.search("Stalker", limit=1)
        assert facts
        fid = facts[0].id
        _set_tier(store.db_path, fid, "cold", "Stalker 2...")

        result = await get_relevant_memories(store, "Stalker", min_score=0.0)
        assert result is not None
        assert "[old memory]" in result

    @pytest.mark.asyncio()
    async def test_archive_facts_not_injected(self, store) -> None:
        # Set all facts to archive
        facts = await store.search("Stalker", limit=10)
        for f in facts:
            _set_tier(store.db_path, f.id, "archive", "archived")

        # Other non-Stalker facts still exist as hot
        result = await get_relevant_memories(store, "Stalker", min_score=0.0)
        if result:
            # If something matched, it should NOT contain archive facts
            assert "archived" not in result

    @pytest.mark.asyncio()
    async def test_matched_warm_fact_promoted_to_hot(self, store) -> None:
        facts = await store.search("Stalker", limit=1)
        assert facts
        fid = facts[0].id
        _set_tier(store.db_path, fid, "warm", "Stalker condensed")

        # Auto-recall should promote back to hot
        await get_relevant_memories(store, "Stalker", min_score=0.0)
        assert _get_tier(store.db_path, fid) == "hot"

    @pytest.mark.asyncio()
    async def test_matched_cold_fact_promoted_to_hot(self, store) -> None:
        facts = await store.search("Kyiv", limit=1)
        assert facts
        fid = facts[0].id
        _set_tier(store.db_path, fid, "cold", "Kyiv...")

        await get_relevant_memories(store, "Kyiv", min_score=0.0)
        assert _get_tier(store.db_path, fid) == "hot"

    @pytest.mark.asyncio()
    async def test_hot_facts_not_promoted_again(self, store) -> None:
        """Hot facts stay hot — no redundant promotion."""
        facts = await store.search("Serhii", limit=1)
        assert facts
        fid = facts[0].id
        assert _get_tier(store.db_path, fid) == "hot"

        # Should work fine, no promotion needed
        result = await get_relevant_memories(store, "Serhii", min_score=0.0)
        assert result is not None
        assert _get_tier(store.db_path, fid) == "hot"
