"""Tests for jarvis.memory.auto_cleanup."""

from __future__ import annotations

import sqlite3

from jarvis.memory.auto_cleanup import AutoCleanup


def _make_db(tmp_path) -> sqlite3.Connection:
    """Create an in-memory test database with the facts schema."""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
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
            fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY (fact_id, tag)
        )
    """)
    return conn


class TestDeduplicate:
    def test_removes_exact_dupes(self, tmp_path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO facts (id, text, access_count) VALUES ('a', 'hello', 0)")
        conn.execute("INSERT INTO facts (id, text, access_count) VALUES ('b', 'hello', 5)")
        conn.execute("INSERT INTO facts (id, text, access_count) VALUES ('c', 'hello', 1)")
        conn.commit()

        cleanup = AutoCleanup()
        removed = cleanup.deduplicate(conn)
        assert removed == 2

        remaining = conn.execute("SELECT id FROM facts").fetchall()
        assert len(remaining) == 1
        assert remaining[0][0] == "b"  # Highest access_count kept

    def test_no_dupes_no_change(self, tmp_path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'unique1')")
        conn.execute("INSERT INTO facts (id, text) VALUES ('b', 'unique2')")
        conn.commit()

        cleanup = AutoCleanup()
        assert cleanup.deduplicate(conn) == 0


class TestArchive:
    def test_archives_old_unused(self, tmp_path) -> None:
        conn = _make_db(tmp_path)
        conn.execute(
            "INSERT INTO facts (id, text, access_count, is_permanent, updated_at) "
            "VALUES ('old', 'ancient fact', 0, 0, '2020-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO facts (id, text, access_count, is_permanent, updated_at) "
            "VALUES ('new', 'fresh fact', 0, 0, datetime('now'))"
        )
        conn.commit()

        cleanup = AutoCleanup(archive_days=365)
        removed = cleanup.archive_old(conn)
        assert removed == 1

        remaining = conn.execute("SELECT id FROM facts").fetchall()
        assert remaining[0][0] == "new"

    def test_permanent_not_archived(self, tmp_path) -> None:
        conn = _make_db(tmp_path)
        conn.execute(
            "INSERT INTO facts (id, text, access_count, is_permanent, updated_at) "
            "VALUES ('perm', 'permanent', 0, 1, '2020-01-01T00:00:00')"
        )
        conn.commit()

        cleanup = AutoCleanup(archive_days=365)
        assert cleanup.archive_old(conn) == 0


class TestRunFull:
    def test_full_cleanup(self, tmp_path) -> None:
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO facts (id, text, access_count) VALUES ('a', 'dup', 0)")
        conn.execute("INSERT INTO facts (id, text, access_count) VALUES ('b', 'dup', 3)")
        conn.execute(
            "INSERT INTO facts (id, text, access_count, updated_at) "
            "VALUES ('c', 'old', 0, '2020-01-01T00:00:00')"
        )
        conn.commit()

        cleanup = AutoCleanup(archive_days=365)
        result = cleanup.run_full(conn)
        assert result["deduplicated"] == 1
        assert result["archived"] == 1
        assert result["compacted"] is True
