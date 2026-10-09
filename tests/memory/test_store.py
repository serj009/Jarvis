"""Tests for jarvis.memory.store (T4.1 MemoryStore)."""

from __future__ import annotations

import pytest

from jarvis.memory.store import Fact, MemoryStore


@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    """Create a temporary MemoryStore for each test."""
    s = MemoryStore(db_path=tmp_path / "test_memory.db")
    await s.open()
    yield s
    await s.close()


class TestMemoryStore:
    @pytest.mark.asyncio()
    async def test_open_creates_database(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "new.db")
        await s.open()
        assert s.is_open
        assert (tmp_path / "new.db").exists()
        await s.close()

    @pytest.mark.asyncio()
    async def test_add_returns_id(self, store) -> None:
        fact_id = await store.add("Test fact", category="general")
        assert isinstance(fact_id, str)
        assert len(fact_id) == 12

    @pytest.mark.asyncio()
    async def test_get_returns_fact(self, store) -> None:
        fact_id = await store.add("User likes pizza", category="personal", tags=["food"])
        fact = await store.get(fact_id)
        assert fact is not None
        assert fact.text == "User likes pizza"
        assert fact.category == "personal"
        assert "food" in fact.tags
        assert fact.access_count == 1

    @pytest.mark.asyncio()
    async def test_get_nonexistent_returns_none(self, store) -> None:
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio()
    async def test_delete(self, store) -> None:
        fact_id = await store.add("Temp fact")
        assert await store.delete(fact_id) is True
        assert await store.get(fact_id) is None

    @pytest.mark.asyncio()
    async def test_delete_nonexistent(self, store) -> None:
        assert await store.delete("nonexistent") is False

    @pytest.mark.asyncio()
    async def test_update(self, store) -> None:
        fact_id = await store.add("Original text")
        assert await store.update(fact_id, "Updated text") is True
        fact = await store.get(fact_id)
        assert fact is not None
        assert fact.text == "Updated text"

    @pytest.mark.asyncio()
    async def test_search_finds_matching(self, store) -> None:
        await store.add("The weather is sunny")
        await store.add("My dog is named Rex")
        await store.add("Weather forecast for tomorrow")

        results = await store.search("weather")
        assert len(results) >= 2

    @pytest.mark.asyncio()
    async def test_search_by_category(self, store) -> None:
        await store.add("Cat fact", category="animals")
        await store.add("Dog fact", category="animals")
        await store.add("Python fact", category="tech")

        results = await store.search("fact", category="animals")
        assert len(results) == 2

    @pytest.mark.asyncio()
    async def test_search_by_tag(self, store) -> None:
        await store.add("Game save location", tags=["games", "saves"])
        await store.add("Game review", tags=["games", "reviews"])
        await store.add("Work meeting", tags=["work"])

        results = await store.search("", tag="games")
        # LIKE "%%" matches everything, so tag filter is the discriminator
        assert all(any("games" in f.tags for _ in [1]) for f in results)

    @pytest.mark.asyncio()
    async def test_count(self, store) -> None:
        assert await store.count() == 0
        await store.add("Fact 1")
        await store.add("Fact 2")
        assert await store.count() == 2

    @pytest.mark.asyncio()
    async def test_count_by_category(self, store) -> None:
        await store.add("A", category="alpha")
        await store.add("B", category="alpha")
        await store.add("C", category="beta")
        assert await store.count(category="alpha") == 2

    @pytest.mark.asyncio()
    async def test_categories(self, store) -> None:
        await store.add("A", category="games")
        await store.add("B", category="games")
        await store.add("C", category="personal")
        cats = await store.categories()
        assert ("games", 2) in cats
        assert ("personal", 1) in cats

    @pytest.mark.asyncio()
    async def test_access_count_increments(self, store) -> None:
        fact_id = await store.add("Accessed fact")
        await store.get(fact_id)
        await store.get(fact_id)
        fact = await store.get(fact_id)
        assert fact is not None
        assert fact.access_count == 3

    @pytest.mark.asyncio()
    async def test_permanent_flag(self, store) -> None:
        fact_id = await store.add("System fact", is_permanent=True)
        fact = await store.get(fact_id)
        assert fact is not None
        assert fact.is_permanent is True

    @pytest.mark.asyncio()
    async def test_tags_stored_lowercase(self, store) -> None:
        fact_id = await store.add("Tagged", tags=["Games", "STEAM"])
        fact = await store.get(fact_id)
        assert fact is not None
        assert "games" in fact.tags
        assert "steam" in fact.tags
