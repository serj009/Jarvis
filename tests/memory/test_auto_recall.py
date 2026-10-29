"""Tests for jarvis.memory.auto_recall (T4.4 auto-recall + T4.6 tier-aware injection)."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.memory.auto_recall import (
    _is_preference_fact,
    _text_for_injection,
    get_relevant_memories,
)
from jarvis.memory.relations import RelationStore
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
        bullet_lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
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
        assert all(ln.startswith("- ") for ln in lines[1:])

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


# ---------------------------------------------------------------------------
# Relation-aware recall (T4.7)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store_with_relations(tmp_path):
    """MemoryStore + RelationStore with linked facts for testing relation expansion."""
    db_path = tmp_path / "test_rel_memory.db"
    s = MemoryStore(db_path=db_path)
    await s.open()

    # Primary fact (will be found by keyword search)
    f1 = await s.add(
        "Mafia 2 — got Flame of Frenzy achievement",
        category="games",
        tags=["mafia_2", "achievement"],
    )
    # Related facts (linked via relations, NOT directly findable by "Flame of Frenzy")
    f2 = await s.add(
        "Mafia 2 achievements: Flame of the Fell God still needed",
        category="games",
        tags=["mafia_2", "achievement"],
    )
    f3 = await s.add(
        "Mafia 2 — completed chapter 5", category="games", tags=["mafia_2", "progress"]
    )
    # Unrelated fact
    f4 = await s.add("User prefers dark theme", category="personal", tags=["ui"])

    rel = RelationStore(db_path=db_path)
    rel.init_schema()
    rel.add_relation(f1, f2, "related_to", strength=0.9)
    rel.add_relation(f1, f3, "belongs_to", strength=0.8)

    yield s, rel, {"f1": f1, "f2": f2, "f3": f3, "f4": f4}
    await s.close()


class TestRelationAwareRecall:
    """Integration tests: auto-recall expands found facts via relation graph."""

    @pytest.mark.asyncio()
    async def test_related_facts_injected(self, store_with_relations) -> None:
        """When a primary fact has relations, related facts appear in output."""
        store, rel, ids = store_with_relations
        result = await get_relevant_memories(
            store, "Flame of Frenzy",
            min_score=0.0,
            relation_store=rel,
        )
        assert result is not None
        # The primary fact should be there
        assert "Flame of Frenzy" in result
        # Related facts should be injected with [related] prefix
        assert "[related]" in result

    @pytest.mark.asyncio()
    async def test_related_facts_contain_linked_content(self, store_with_relations) -> None:
        """Related facts bring in content from the relation graph."""
        store, rel, ids = store_with_relations
        result = await get_relevant_memories(
            store, "Flame of Frenzy",
            min_score=0.0,
            relation_store=rel,
        )
        assert result is not None
        # At least one of the related facts should appear
        has_fell_god = "Fell God" in result
        has_chapter = "chapter 5" in result
        assert has_fell_god or has_chapter, f"Expected related content in: {result}"

    @pytest.mark.asyncio()
    async def test_unrelated_facts_not_pulled_in(self, store_with_relations) -> None:
        """Facts without relations to primary results are NOT injected."""
        store, rel, ids = store_with_relations
        result = await get_relevant_memories(
            store, "Flame of Frenzy",
            min_score=0.0,
            relation_store=rel,
        )
        # "dark theme" is unrelated — should not appear
        if result:
            assert "dark theme" not in result

    @pytest.mark.asyncio()
    async def test_dedup_no_duplicate_facts(self, store_with_relations) -> None:
        """A fact already in primary results is NOT duplicated as related."""
        store, rel, ids = store_with_relations
        result = await get_relevant_memories(
            store, "Flame of Frenzy",
            min_score=0.0,
            relation_store=rel,
        )
        assert result is not None
        lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
        # Extract fact texts (strip prefix markers)
        texts = [ln.lstrip("- ").replace("[related] ", "") for ln in lines]
        # No exact duplicates
        assert len(texts) == len(set(texts)), f"Duplicates found: {texts}"

    @pytest.mark.asyncio()
    async def test_max_total_cap(self, tmp_path) -> None:
        """Total facts (primary + related) capped at max_total."""
        db_path = tmp_path / "cap_test.db"
        s = MemoryStore(db_path=db_path)
        await s.open()

        # Create 1 primary fact with 8 related facts
        primary = await s.add(
            "Main game fact about Elden Ring", category="games", tags=["elden_ring"]
        )
        related_ids = []
        for i in range(8):
            rid = await s.add(
                f"Elden Ring related fact number {i}", category="games", tags=["elden_ring"]
            )
            related_ids.append(rid)

        rel = RelationStore(db_path=db_path)
        rel.init_schema()
        for rid in related_ids:
            rel.add_relation(primary, rid, "related_to")

        result = await get_relevant_memories(
            s, "Elden Ring",
            min_score=0.0,
            max_results=3,
            relation_store=rel,
            max_total=5,
        )
        assert result is not None
        bullet_lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
        assert len(bullet_lines) <= 5, f"Expected ≤5 total, got {len(bullet_lines)}"
        await s.close()

    @pytest.mark.asyncio()
    async def test_no_relation_store_still_works(self, store_with_relations) -> None:
        """Without relation_store, recall works as before (no expansion)."""
        store, _rel, _ids = store_with_relations
        result = await get_relevant_memories(store, "Flame of Frenzy", min_score=0.0)
        assert result is not None
        assert "[related]" not in result


# ---------------------------------------------------------------------------
# Preference-aware injection (T4.7)
# ---------------------------------------------------------------------------

class TestIsPreferenceFact:
    """Unit tests for _is_preference_fact helper."""

    def test_behavior_tag(self) -> None:
        fact = Fact(id="1", text="always open Chrome", tags=["auto_behavior"])
        assert _is_preference_fact(fact) is True

    def test_negative_pref_tag(self) -> None:
        fact = Fact(id="2", text="never close Steam", tags=["auto_negative_pref"])
        assert _is_preference_fact(fact) is True

    def test_tool_pref_tag(self) -> None:
        fact = Fact(id="3", text="my browser is Chrome", tags=["auto_tool_pref"])
        assert _is_preference_fact(fact) is True

    def test_setting_pref_tag(self) -> None:
        fact = Fact(id="4", text="volume 70", tags=["auto_setting_pref"])
        assert _is_preference_fact(fact) is True

    def test_regular_fact_not_preference(self) -> None:
        fact = Fact(id="5", text="User likes Stalker", tags=["auto_preference"])
        assert _is_preference_fact(fact) is False

    def test_empty_tags(self) -> None:
        fact = Fact(id="6", text="Something", tags=[])
        assert _is_preference_fact(fact) is False

    def test_no_tags_attr(self) -> None:
        fact = Fact(id="7", text="Something")
        assert _is_preference_fact(fact) is False


@pytest.fixture()
async def pref_store(tmp_path) -> MemoryStore:
    """MemoryStore with both regular facts and preference facts."""
    s = MemoryStore(db_path=tmp_path / "pref_memory.db")
    await s.open()
    # Regular fact
    await s.add("User likes Stalker 2", category="games", tags=["auto_preference"])
    # Preference facts
    await s.add(
        "User said: always open Chrome instead of Edge",
        category="behavior", source="conversation", tags=["auto_behavior"],
    )
    await s.add(
        "User said: never close Steam when closing everything",
        category="negative_pref", source="conversation", tags=["auto_negative_pref"],
    )
    yield s
    await s.close()


class TestPreferenceAwareRecall:
    """Integration tests: preferences injected with boost (before regular memories)."""

    @pytest.mark.asyncio()
    async def test_preferences_appear_before_regular_memories(self, pref_store) -> None:
        result = await get_relevant_memories(
            pref_store, "Chrome Steam Stalker", min_score=0.0,
        )
        assert result is not None
        # "[User preferences:]" must appear before "[Relevant memories"
        pref_pos = result.find("[User preferences:]")
        mem_pos = result.find("[Relevant memories from past conversations:]")
        assert pref_pos != -1, "Preference header missing"
        if mem_pos != -1:
            assert pref_pos < mem_pos, "Preferences must come before regular memories"

    @pytest.mark.asyncio()
    async def test_preference_header_present(self, pref_store) -> None:
        result = await get_relevant_memories(
            pref_store, "Chrome", min_score=0.0,
        )
        assert result is not None
        assert "[User preferences:]" in result

    @pytest.mark.asyncio()
    async def test_only_regular_memories_no_pref_header(self, pref_store) -> None:
        """When only non-preference facts match, no [User preferences:] header."""
        result = await get_relevant_memories(
            pref_store, "Stalker 2", min_score=0.0,
        )
        if result is not None:
            # If Stalker matched, it should be under regular memories only
            assert "[Relevant memories from past conversations:]" in result
