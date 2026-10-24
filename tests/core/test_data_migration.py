"""Tests for the one-time ~/.jarvis -> data-dir migration (Task 4+B).

All tests mock sys.platform='win32', repoint the legacy root via the module's
_legacy_root() seam, and repoint the new data root via JARVIS_DATA_DIR. No
real data directories are touched.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from jarvis.core import data_migration as dm


@pytest.fixture
def _win(monkeypatch):
    monkeypatch.setattr(dm.sys, "platform", "win32")


def _legacy_and_new(monkeypatch, tmp_path):
    legacy = tmp_path / "dotjarvis"
    new_root = tmp_path / "appdata_jarvis"
    legacy.mkdir()
    new_root.mkdir()
    monkeypatch.setattr(dm, "_legacy_root", lambda: legacy)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(new_root))
    return legacy, new_root


def _make_db(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()


def test_copies_plain_and_db_files(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "config.json").write_text('{"a": 1}', encoding="utf-8")
    (legacy / "memory").mkdir()
    _make_db(legacy / "memory" / "memory.db", "hello")

    assert dm.migrate_from_dotjarvis() is True

    # Plain file copied verbatim.
    assert (new_root / "config.json").read_text(encoding="utf-8") == '{"a": 1}'
    # DB copied and readable with the same content.
    dst_db = new_root / "memory" / "memory.db"
    assert dst_db.exists()
    con = sqlite3.connect(str(dst_db))
    assert con.execute("SELECT v FROM t").fetchone()[0] == "hello"
    con.close()
    # Marker written.
    assert (new_root / ".migrated_from_dotjarvis").exists()


def test_does_not_delete_legacy(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("x", encoding="utf-8")
    dm.migrate_from_dotjarvis()
    # Legacy file still there — copy, not move.
    assert (legacy / "config.json").exists()


def test_db_with_wal_copies_consistently(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    db = legacy / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('wal-row')")
    con.commit()
    # Leave the connection OPEN so -wal/-shm sidecars exist during migration.
    try:
        assert dm.migrate_from_dotjarvis() is True
        dst = new_root / "memory" / "memory.db"
        assert dst.exists()
        c2 = sqlite3.connect(str(dst))
        assert c2.execute("SELECT v FROM t").fetchone()[0] == "wal-row"
        c2.close()
        # Sidecars must NOT be copied (backup folds them in).
        assert not (new_root / "memory" / "memory.db-wal").exists()
        assert not (new_root / "memory" / "memory.db-shm").exists()
    finally:
        con.close()


def test_idempotent_marker_short_circuits(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "a.json").write_text("first", encoding="utf-8")
    assert dm.migrate_from_dotjarvis() is True
    # Second run must not re-copy (marker present). Add a new legacy file and
    # confirm it is NOT migrated.
    (legacy / "b.json").write_text("second", encoding="utf-8")
    assert dm.migrate_from_dotjarvis() is True
    assert not (new_root / "b.json").exists()


def test_conflict_keeps_new_file(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("LEGACY", encoding="utf-8")
    (new_root / "config.json").write_text("NEW", encoding="utf-8")
    dm.migrate_from_dotjarvis()
    # Existing new file is never overwritten.
    assert (new_root / "config.json").read_text(encoding="utf-8") == "NEW"


def test_voices_dir_not_copied(_win, monkeypatch, tmp_path):
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "voices").mkdir()
    (legacy / "voices" / "big_model.onnx").write_bytes(b"x" * 1000)
    dm.migrate_from_dotjarvis()
    assert not (new_root / "voices").exists()


def test_noop_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(dm.sys, "platform", "linux")
    legacy = tmp_path / "dotjarvis"
    legacy.mkdir()
    (legacy / "config.json").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dm, "_legacy_root", lambda: legacy)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "new"))
    assert dm.migrate_from_dotjarvis() is False


# ---------------------------------------------------------------------------
# Marker-only-on-success + retry (Task 4+B addendum)
# ---------------------------------------------------------------------------


def test_plain_copy_failure_withholds_marker_then_retry_succeeds(
    _win, monkeypatch, tmp_path
):
    """A failed plain copy must NOT write the marker; the next run retries it.

    Pass 1: the copy fails -> failed=1, marker absent, file missing.
    Pass 2 (copy works): the missing file is copied and the marker written.
    A stateful wrapper fails only on its first call, so both passes run under
    the same monkeypatch environment (no undo needed).
    """
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("payload", encoding="utf-8")
    marker = new_root / ".migrated_from_dotjarvis"

    real_copy_plain = dm._copy_plain
    state = {"first": True}

    def flaky_copy_plain(src, dst):
        if state["first"]:
            state["first"] = False
            return False
        return real_copy_plain(src, dst)

    monkeypatch.setattr(dm, "_copy_plain", flaky_copy_plain)

    # Pass 1: copy fails.
    assert dm.migrate_from_dotjarvis() is True
    assert not marker.exists()
    assert not (new_root / "config.json").exists()

    # Pass 2: copy succeeds -> file present, marker written.
    assert dm.migrate_from_dotjarvis() is True
    assert (new_root / "config.json").read_text(encoding="utf-8") == "payload"
    assert marker.exists()


def test_sqlite_backup_failure_withholds_marker_then_retry_succeeds(
    _win, monkeypatch, tmp_path
):
    """A failed SQLite copy behaves like a plain failure: no marker, retry."""
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    _make_db(legacy / "memory" / "memory.db", "dbval")
    marker = new_root / ".migrated_from_dotjarvis"

    real_copy_sqlite = dm._copy_sqlite
    state = {"first": True}

    def flaky_copy_sqlite(src, dst):
        if state["first"]:
            state["first"] = False
            return False
        return real_copy_sqlite(src, dst)

    monkeypatch.setattr(dm, "_copy_sqlite", flaky_copy_sqlite)

    assert dm.migrate_from_dotjarvis() is True
    assert not marker.exists()
    assert not (new_root / "memory" / "memory.db").exists()

    assert dm.migrate_from_dotjarvis() is True
    dst = new_root / "memory" / "memory.db"
    assert dst.exists()
    con = sqlite3.connect(str(dst))
    assert con.execute("SELECT v FROM t").fetchone()[0] == "dbval"
    con.close()
    assert marker.exists()


def test_conflict_logged_at_warning(_win, monkeypatch, tmp_path, caplog):
    """Differing file present in both roots is logged at WARNING, not INFO."""
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("LEGACY-LONGER", encoding="utf-8")
    (new_root / "config.json").write_text("NEW", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="jarvis.core.data_migration"):
        dm.migrate_from_dotjarvis()
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "conflict" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "config.json" in warnings[0].getMessage()


def test_no_failures_writes_marker_and_counts(_win, monkeypatch, tmp_path, caplog):
    """When failed==0 behaviour is unchanged: marker written, summary logged."""
    legacy, new_root = _legacy_and_new(monkeypatch, tmp_path)
    (legacy / "a.json").write_text("1", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="jarvis.core.data_migration"):
        assert dm.migrate_from_dotjarvis() is True
    assert (new_root / ".migrated_from_dotjarvis").exists()
    summary = [r for r in caplog.records if "complete:" in r.getMessage()]
    assert summary, "expected a completion summary line"
    assert "failed" in summary[-1].getMessage()
