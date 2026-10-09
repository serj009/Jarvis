"""Tests for jarvis.memory.relations (RelationStore).

Tests relation CRUD, graph traversal, tag-based discovery, and
integration with the MemoryStore schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.relations import RelationStore
from jarvis.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path) -> Path:
    """Create a temp database with facts + relations schema."""
    path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Create facts and fact_tags tables (same as MemoryStore).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id              TEXT PRIMARY KEY,
            text            TEXT NOT NULL,
            category        TEXT NOT NULL DEFAULT 'general',
            source          TEXT NOT NULL DEFAULT 'user',
            language        TEXT NOT NULL DEFAULT 'en',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            access_count    INTEGER NOT NULL DEFAULT 0,
            last_accessed_at TEXT,
            is_permanent    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fact_tags (
            fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
            tag     TEXT NOT NULL,
            PRIMARY KEY (fact_id, tag)
        );
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def relation_store(db_path) -> RelationStore:
    """Create a RelationStore with initialized schema."""
    store = RelationStore(db_path=db_path)
    store.init_schema()
    return store


def _insert_fact(db_path: Path, fact_id: str, text: str, tags: list[str] | None = None) -> None:
    """Helper to insert a fact directly into the DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO facts (id, text, category, source, language, created_at, updated_at) "
        "VALUES (?, ?, 'general', 'user', 'en', datetime('now'), datetime('now'))",
        (fact_id, text),
    )
    for tag in (tags or []):
        conn.execute(
            "INSERT INTO fact_tags (fact_id, tag) VALUES (?, ?)",
            (fact_id, tag.lower()),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestRelationStoreSchema:
    def test_init_schema_creates_table(self, db_path) -> None:
        store = RelationStore(db_path=db_path)
        store.init_schema()
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "relations" in tables

    def test_init_schema_idempotent(self, db_path) -> None:
        store = RelationStore(db_path=db_path)
        store.init_schema()
        store.init_schema()  # Should not raise.

    def test_indexes_created(self, db_path) -> None:
        store = RelationStore(db_path=db_path)
        store.init_schema()
        conn = sqlite3.connect(str(db_path))
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()]
        conn.close()
        assert "idx_rel_from" in indexes
        assert "idx_rel_to" in indexes
        assert "idx_rel_type" in indexes


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------

class TestRelationCRUD:
    def test_add_relation(self, relation_store, db_path) -> None:
        _insert_fact(db_path, "fact_a", "Stalker 2 is a game")
        _insert_fact(db_path, "fact_b", "Achievement Doslidnyk in Stalker 2")

        rel_id = relation_store.add_relation("fact_b", "fact_a", "belongs_to", 0.9)
        assert isinstance(rel_id, int)
        assert rel_id > 0

    def test_add_unknown_relation_type_warns(self, relation_store, db_path) -> None:
        """Unknown types are accepted with a warning (forward-compatible)."""
        _insert_fact(db_path, "f1", "Fact 1")
        _insert_fact(db_path, "f2", "Fact 2")
        # Should not raise; just warns.
        rel_id = relation_store.add_relation("f1", "f2", "custom_type")
        assert rel_id > 0

    def test_remove_relation(self, relation_store, db_path) -> None:
        _insert_fact(db_path, "f1", "Fact 1")
        _insert_fact(db_path, "f2", "Fact 2")
        rel_id = relation_store.add_relation("f1", "f2", "related_to")
        assert relation_store.remove_relation(rel_id) is True
        assert relation_store.remove_relation(rel_id) is False  # Already removed.

    def test_count(self, relation_store, db_path) -> None:
        assert relation_store.count() == 0
        _insert_fact(db_path, "f1", "A")
        _insert_fact(db_path, "f2", "B")
        relation_store.add_relation("f1", "f2", "related_to")
        assert relation_store.count() == 1


# ---------------------------------------------------------------------------
# Graph traversal tests
# ---------------------------------------------------------------------------

class TestGraphTraversal:
    def test_get_related_depth_1(self, relation_store, db_path) -> None:
        """Depth-1 traversal finds direct relations."""
        _insert_fact(db_path, "game", "Stalker 2")
        _insert_fact(db_path, "ach1", "Achievement Doslidnyk")
        _insert_fact(db_path, "ach2", "Achievement Voyin")

        relation_store.add_relation("ach1", "game", "belongs_to")
        relation_store.add_relation("ach2", "game", "belongs_to")

        related = relation_store.get_related("game", depth=1)
        # Should find reverse edges from ach1 and ach2.
        related_from_ids = {r["fact_id_from"] for r in related}
        assert "ach1" in related_from_ids
        assert "ach2" in related_from_ids

    def test_get_related_depth_2(self, relation_store, db_path) -> None:
        """Depth-2 traversal finds relations of relations."""
        _insert_fact(db_path, "game", "Stalker 2")
        _insert_fact(db_path, "ach1", "Achievement Doslidnyk")
        _insert_fact(db_path, "loc1", "Location Pripyat")

        relation_store.add_relation("ach1", "game", "belongs_to")
        relation_store.add_relation("loc1", "ach1", "related_to")

        related = relation_store.get_related("game", depth=2)
        all_ids = set()
        for r in related:
            all_ids.add(r.get("fact_id_from", ""))
            all_ids.add(r.get("fact_id_to", ""))
        # Should find loc1 through ach1.
        assert "loc1" in all_ids

    def test_get_related_filtered_by_type(self, relation_store, db_path) -> None:
        _insert_fact(db_path, "f1", "Fact 1")
        _insert_fact(db_path, "f2", "Fact 2")
        _insert_fact(db_path, "f3", "Fact 3")

        relation_store.add_relation("f1", "f2", "belongs_to")
        relation_store.add_relation("f1", "f3", "related_to")

        belongs = relation_store.get_related("f1", relation_type="belongs_to", depth=1)
        assert len(belongs) == 1
        assert belongs[0]["fact_id_to"] == "f2"

    def test_get_related_empty(self, relation_store, db_path) -> None:
        _insert_fact(db_path, "lone", "Lonely fact")
        related = relation_store.get_related("lone", depth=1)
        assert related == []

    def test_no_infinite_loop(self, relation_store, db_path) -> None:
        """Circular relations don't cause infinite traversal."""
        _insert_fact(db_path, "a", "Fact A")
        _insert_fact(db_path, "b", "Fact B")
        _insert_fact(db_path, "c", "Fact C")

        relation_store.add_relation("a", "b", "related_to")
        relation_store.add_relation("b", "c", "related_to")
        relation_store.add_relation("c", "a", "related_to")  # Cycle!

        # Should terminate without error.
        related = relation_store.get_related("a", depth=3)
        assert isinstance(related, list)


# ---------------------------------------------------------------------------
# Tag-based discovery tests
# ---------------------------------------------------------------------------

class TestFindAllRelatedToTag:
    def test_find_all_related_to_tag(self, relation_store, db_path) -> None:
        """Finds facts related to a tag through relation chains."""
        _insert_fact(db_path, "game", "Stalker 2 is a game", tags=["stalker_2", "games"])
        _insert_fact(db_path, "ach1", "Achievement Doslidnyk", tags=["stalker_2"])
        _insert_fact(db_path, "weapon", "AK-47 in Stalker")

        relation_store.add_relation("ach1", "game", "belongs_to")
        relation_store.add_relation("weapon", "game", "belongs_to")

        all_ids = relation_store.find_all_related_to_tag("stalker_2")
        assert "game" in all_ids
        assert "ach1" in all_ids
        assert "weapon" in all_ids  # Found through relation graph.

    def test_find_all_related_to_tag_empty(self, relation_store, db_path) -> None:
        all_ids = relation_store.find_all_related_to_tag("nonexistent")
        assert all_ids == []

    def test_find_all_related_tag_no_relations(self, relation_store, db_path) -> None:
        """Facts with the tag but no relations are still found."""
        _insert_fact(db_path, "f1", "Some fact", tags=["python"])
        all_ids = relation_store.find_all_related_to_tag("python")
        assert "f1" in all_ids


# ---------------------------------------------------------------------------
# get_relations_for_fact tests
# ---------------------------------------------------------------------------

class TestGetRelationsForFact:
    def test_get_relations_both_directions(self, relation_store, db_path) -> None:
        _insert_fact(db_path, "a", "Fact A")
        _insert_fact(db_path, "b", "Fact B")
        _insert_fact(db_path, "c", "Fact C")

        relation_store.add_relation("a", "b", "related_to")
        relation_store.add_relation("c", "a", "depends_on")

        rels = relation_store.get_relations_for_fact("a")
        assert len(rels) == 2


# ---------------------------------------------------------------------------
# Integration with MemoryStore
# ---------------------------------------------------------------------------

class TestRelationStoreWithMemoryStore:
    @pytest.mark.asyncio()
    async def test_relations_table_created_by_store_open(self, tmp_path) -> None:
        """MemoryStore.open() creates the relations table."""
        store = MemoryStore(db_path=tmp_path / "integration.db")
        await store.open()

        conn = sqlite3.connect(str(tmp_path / "integration.db"))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        await store.close()

        assert "relations" in tables
