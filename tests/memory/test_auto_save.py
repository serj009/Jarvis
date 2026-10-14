"""Tests for jarvis.memory.auto_save (T4.4 automatic fact extraction)."""

from __future__ import annotations

import pytest

from jarvis.memory.auto_save import auto_save_facts, extract_facts
from jarvis.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# extract_facts (synchronous, no store needed)
# ---------------------------------------------------------------------------

class TestExtractFacts:
    def test_english_preference_i_like(self) -> None:
        facts = extract_facts("I like jazz music")
        assert len(facts) >= 1
        assert any("jazz" in f[0].lower() for f in facts)
        assert facts[0][1] == "preference"

    def test_english_preference_my_favorite(self) -> None:
        facts = extract_facts("my favorite game is Stalker")
        assert len(facts) >= 1
        assert any("stalker" in f[0].lower() for f in facts)

    def test_english_preference_i_prefer(self) -> None:
        facts = extract_facts("I prefer dark chocolate")
        assert len(facts) >= 1
        assert facts[0][1] == "preference"

    def test_russian_preference(self) -> None:
        facts = extract_facts("я люблю рок музыку")
        assert len(facts) >= 1
        assert facts[0][1] == "preference"

    def test_ukrainian_preference(self) -> None:
        facts = extract_facts("мені подобається піца")
        assert len(facts) >= 1
        assert facts[0][1] == "preference"

    def test_english_identity(self) -> None:
        facts = extract_facts("my name is Serhii")
        assert len(facts) >= 1
        assert any(f[1] == "identity" for f in facts)
        assert any("serhii" in f[0].lower() for f in facts)

    def test_russian_identity(self) -> None:
        facts = extract_facts("меня зовут Сергей")
        assert len(facts) >= 1
        assert any(f[1] == "identity" for f in facts)

    def test_english_location(self) -> None:
        facts = extract_facts("I live in London")
        assert len(facts) >= 1
        assert any(f[1] == "location" for f in facts)

    def test_russian_location(self) -> None:
        facts = extract_facts("я живу в Киеве")
        assert len(facts) >= 1
        assert any(f[1] == "location" for f in facts)

    def test_explicit_remember(self) -> None:
        facts = extract_facts("запомни что мой пароль от wifi 12345")
        assert len(facts) >= 1
        assert any(f[1] == "explicit" for f in facts)

    def test_no_facts_in_question(self) -> None:
        facts = extract_facts("what is the weather today?")
        assert len(facts) == 0

    def test_no_facts_in_command(self) -> None:
        facts = extract_facts("open chrome")
        assert len(facts) == 0

    def test_short_match_filtered(self) -> None:
        # "I like it" -> "it" is too short (< 4 chars)
        facts = extract_facts("I like it")
        assert len(facts) == 0

    def test_strips_trailing_punctuation(self) -> None:
        facts = extract_facts("I like pizza!!")
        assert len(facts) >= 1
        assert not facts[0][0].endswith("!")

    def test_work_english(self) -> None:
        facts = extract_facts("I work as a data scientist")
        assert len(facts) >= 1
        assert any(f[1] == "work" for f in facts)


# ---------------------------------------------------------------------------
# auto_save_facts (async, needs store)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


class TestAutoSaveFacts:
    @pytest.mark.asyncio()
    async def test_saves_preference(self, store) -> None:
        saved = await auto_save_facts(store, "I like jazz")
        assert saved >= 1
        # Verify it's in the store
        facts = await store.search("jazz", limit=5)
        assert len(facts) >= 1
        assert any("jazz" in f.text.lower() for f in facts)

    @pytest.mark.asyncio()
    async def test_saves_identity(self, store) -> None:
        saved = await auto_save_facts(store, "my name is Serhii")
        assert saved >= 1
        facts = await store.search("Serhii", limit=5)
        assert len(facts) >= 1

    @pytest.mark.asyncio()
    async def test_no_save_for_question(self, store) -> None:
        saved = await auto_save_facts(store, "what's the weather?")
        assert saved == 0

    @pytest.mark.asyncio()
    async def test_dedup_prevents_double_save(self, store) -> None:
        saved1 = await auto_save_facts(store, "I like pizza")
        saved2 = await auto_save_facts(store, "I like pizza")
        assert saved1 >= 1
        assert saved2 == 0  # duplicate should be skipped

    @pytest.mark.asyncio()
    async def test_returns_zero_when_store_closed(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "closed.db")
        # Not opened
        saved = await auto_save_facts(s, "I like jazz")
        assert saved == 0

    @pytest.mark.asyncio()
    async def test_saved_fact_has_auto_tag(self, store) -> None:
        await auto_save_facts(store, "I like rock music")
        facts = await store.search("rock", limit=5)
        assert len(facts) >= 1
        assert any("auto_preference" in f.tags for f in facts)

    @pytest.mark.asyncio()
    async def test_saved_fact_has_conversation_source(self, store) -> None:
        await auto_save_facts(store, "I live in Berlin")
        facts = await store.search("Berlin", limit=5)
        assert len(facts) >= 1
        assert facts[0].source == "conversation"
