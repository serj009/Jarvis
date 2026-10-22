"""Tests for T12.2 — Memory DB backup / restore (MemoryStore + AutoCleanup)."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.memory.auto_cleanup import AutoCleanup, backup_before_cleanup
from jarvis.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    """Temporary MemoryStore for each test."""
    s = MemoryStore(db_path=tmp_path / "test_memory.db")
    await s.open()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# MemoryStore.backup_database / restore_from_backup
# ---------------------------------------------------------------------------

class TestMemoryStoreBackup:
    @pytest.mark.asyncio()
    async def test_backup_creates_bak_file(self, store, tmp_path) -> None:
        await store.add("important fact")
        bak = await store.backup_database()
        assert bak.exists()
        assert bak.suffix == ".bak"
        assert bak.stat().st_size > 0

    @pytest.mark.asyncio()
    async def test_restore_recovers_after_corruption(self, store, tmp_path) -> None:
        """Add data → backup → corrupt DB → restore → data is back."""
        fact_id = await store.add("precious data")
        await store.backup_database()

        # Corrupt the DB by overwriting with garbage.
        await store.close()
        store.db_path.write_bytes(b"CORRUPT" * 100)

        # Restore from backup.
        restored = await store.restore_from_backup()
        assert restored is True

        # Re-open and verify the original fact survived.
        await store.open()
        fact = await store.get(fact_id)
        assert fact is not None
        assert fact.text == "precious data"

    @pytest.mark.asyncio()
    async def test_restore_returns_false_when_no_backup(self, tmp_path) -> None:
        s = MemoryStore(db_path=tmp_path / "fresh.db")
        await s.open()
        assert await s.restore_from_backup() is False
        await s.close()

    @pytest.mark.asyncio()
    async def test_backup_is_wal_safe(self, store, tmp_path) -> None:
        """Backup must be self-contained (no -wal dependency)."""
        await store.add("wal test fact")
        bak = await store.backup_database()

        # The backup should be readable on its own without -wal/-shm.
        conn = sqlite3.connect(str(bak))
        rows = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        conn.close()
        assert rows[0] >= 1

    @pytest.mark.asyncio()
    async def test_restore_removes_stale_wal_shm(self, store, tmp_path) -> None:
        await store.add("fact")
        await store.backup_database()
        await store.close()

        # Create fake stale WAL/SHM files.
        wal = store.db_path.with_suffix(".db-wal")
        shm = store.db_path.with_suffix(".db-shm")
        wal.write_bytes(b"stale-wal")
        shm.write_bytes(b"stale-shm")

        await store.restore_from_backup()
        assert not wal.exists()
        assert not shm.exists()


# ---------------------------------------------------------------------------
# backup_before_cleanup (standalone helper)
# ---------------------------------------------------------------------------

class TestBackupBeforeCleanup:
    def test_creates_backup(self, tmp_path) -> None:
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()

        bak = backup_before_cleanup(db)
        assert bak is not None
        assert bak.exists()

    def test_returns_none_for_missing_db(self, tmp_path) -> None:
        assert backup_before_cleanup(tmp_path / "nope.db") is None


# ---------------------------------------------------------------------------
# AutoCleanup.run_full with db_path triggers backup
# ---------------------------------------------------------------------------

class TestCleanupBackupIntegration:
    def test_run_full_with_db_path_creates_backup(self, tmp_path) -> None:
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE facts (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                source TEXT NOT NULL DEFAULT 'user',
                language TEXT NOT NULL DEFAULT 'en',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT,
                is_permanent INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE fact_tags (
                fact_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (fact_id, tag)
            )
        """)
        conn.commit()

        cleanup = AutoCleanup()
        cleanup.run_full(conn, db_path=db)
        conn.close()

        bak = db.with_suffix(".db.bak")
        assert bak.exists()

    def test_run_full_without_db_path_still_works(self, tmp_path) -> None:
        """Backward compat: run_full without db_path must not break."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE facts (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                source TEXT NOT NULL DEFAULT 'user',
                language TEXT NOT NULL DEFAULT 'en',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT,
                is_permanent INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE fact_tags (
                fact_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (fact_id, tag)
            )
        """)
        conn.commit()

        cleanup = AutoCleanup()
        result = cleanup.run_full(conn)
        conn.close()
        assert "deduplicated" in result
