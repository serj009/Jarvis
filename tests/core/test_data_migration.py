"""Tests for the one-time ~/.jarvis -> data-dir migration (Task 4+B).

All tests mock sys.platform='win32', repoint the legacy root via the module's
_legacy_root() seam, and repoint the new data root via JARVIS_DATA_DIR. No
real data directories are touched.
"""

from __future__ import annotations

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
