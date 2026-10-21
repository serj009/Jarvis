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


def _make_db_with_metadata(tmp_path) -> sqlite3.Connection:
    """DB with the current schema: metadata columns + relations table."""
    conn = sqlite3.connect(str(tmp_path / "integrity.db"))
    conn.executescript("""
        CREATE TABLE facts (
            id TEXT PRIMARY KEY, text TEXT NOT NULL,
            category TEXT DEFAULT 'general', source TEXT DEFAULT 'user',
            language TEXT DEFAULT 'en',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0, last_accessed_at TEXT,
            is_permanent INTEGER DEFAULT 0,
            tier TEXT DEFAULT 'hot', condensed_text TEXT,
            importance REAL DEFAULT 0.5, confidence REAL DEFAULT 1.0,
            valid_until TEXT, status TEXT DEFAULT 'active'
        );
        CREATE TABLE fact_tags (
            fact_id TEXT NOT NULL, tag TEXT NOT NULL,
            PRIMARY KEY (fact_id, tag)
        );
        CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id_from TEXT NOT NULL, fact_id_to TEXT NOT NULL,
            relation_type TEXT NOT NULL, strength REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    return conn


_NOW = "2026-10-21T00:00:00+00:00"


class TestIntegrityReport:
    def test_detects_untagged_active_facts(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('t', 'tagged')")
        conn.execute("INSERT INTO fact_tags VALUES ('t', 'sometag')")
        conn.execute("INSERT INTO facts (id, text) VALUES ('u', 'untagged')")
        conn.commit()

        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert report["untagged"] == ["u"]

    def test_ignores_superseded_in_untagged(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute(
            "INSERT INTO facts (id, text, status) VALUES ('s', 'old', 'superseded')"
        )
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert "s" not in report["untagged"]

    def test_detects_broken_relations(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'A')")
        conn.execute("INSERT INTO fact_tags VALUES ('a', 'x')")
        conn.execute(
            "INSERT INTO relations (fact_id_from, fact_id_to, relation_type) "
            "VALUES ('a', 'ghost', 'related_to')"
        )
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert len(report["broken_relations"]) == 1
        assert report["broken_relations"][0]["to"] == "ghost"

    def test_detects_unresolved_contradictions(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        for fid in ("a", "b"):
            conn.execute("INSERT INTO facts (id, text) VALUES (?, ?)", (fid, fid))
            conn.execute("INSERT INTO fact_tags VALUES (?, 'x')", (fid,))
        conn.execute(
            "INSERT INTO relations (fact_id_from, fact_id_to, relation_type) "
            "VALUES ('a', 'b', 'contradicts')"
        )
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert report["unresolved_contradictions"] == [{"from": "a", "to": "b"}]

    def test_resolved_contradiction_not_flagged(self, tmp_path) -> None:
        """Once one side is superseded, the contradiction is resolved."""
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'A')")
        conn.execute("INSERT INTO fact_tags VALUES ('a', 'x')")
        conn.execute(
            "INSERT INTO facts (id, text, status) VALUES ('b', 'B', 'superseded')"
        )
        conn.execute(
            "INSERT INTO relations (fact_id_from, fact_id_to, relation_type) "
            "VALUES ('a', 'b', 'contradicts')"
        )
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert report["unresolved_contradictions"] == []

    def test_detects_expired_facts(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'A')")
        conn.execute("INSERT INTO fact_tags VALUES ('a', 'x')")
        conn.execute(
            "INSERT INTO facts (id, text, valid_until) "
            "VALUES ('e', 'expired', '2020-01-01T00:00:00+00:00')"
        )
        conn.execute("INSERT INTO fact_tags VALUES ('e', 'x')")
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert report["expired"] == ["e"]

    def test_clean_db_reports_zero_issues(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'A')")
        conn.execute("INSERT INTO fact_tags VALUES ('a', 'x')")
        conn.commit()
        report = AutoCleanup().integrity_report(conn, now=_NOW)
        assert report["total_issues"] == 0


class TestPruneBrokenRelations:
    def test_removes_dangling_edges_only(self, tmp_path) -> None:
        conn = _make_db_with_metadata(tmp_path)
        conn.execute("INSERT INTO facts (id, text) VALUES ('a', 'A')")
        conn.execute("INSERT INTO facts (id, text) VALUES ('b', 'B')")
        conn.execute(
            "INSERT INTO relations (fact_id_from, fact_id_to, relation_type) "
            "VALUES ('a', 'b', 'related_to')"  # valid
        )
        conn.execute(
            "INSERT INTO relations (fact_id_from, fact_id_to, relation_type) "
            "VALUES ('a', 'ghost', 'related_to')"  # dangling
        )
        conn.commit()
        removed = AutoCleanup().prune_broken_relations(conn)
        assert removed == 1
        remaining = conn.execute("SELECT fact_id_to FROM relations").fetchall()
        assert remaining == [("b",)]
