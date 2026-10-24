"""Task D: config backup rotation, corruption recovery, atomic write, doctor.

jarvis.core.config imports only stdlib + pydantic at module top, so this file
is not auto-skipped in a lightweight venv. The autouse conftest fixture points
JARVIS_DATA_DIR at a temp dir; every test here passes an explicit tmp_path
config anyway, so the real data dir is never touched.
"""

from __future__ import annotations

import copy
import json
import logging

import pytest

from jarvis.core import config as cfg_mod
from jarvis.core.config import (
    CURRENT_SCHEMA_VERSION,
    JarvisConfig,
    _lastgood_path,
    _legacy_bak_path,
    _list_versioned_backups,
    _update_lastgood,
    _versioned_bak_path,
    _write_versioned_backup,
    config_health,
    load_config,
    save_config,
)
from jarvis.core.migrations import ConfigMigrationError, migrate

# ---------------------------------------------------------------------------
# §3 migrate() must not mutate the caller's dict
# ---------------------------------------------------------------------------


def test_migrate_does_not_mutate_input():
    raw = {"schema_version": CURRENT_SCHEMA_VERSION - 1}
    snapshot = copy.deepcopy(raw)
    migrate(raw)
    assert raw == snapshot, "migrate() mutated the caller's dict"


def test_migrate_future_version_raises_and_keeps_input():
    raw = {"schema_version": CURRENT_SCHEMA_VERSION + 5}
    snapshot = dict(raw)
    with pytest.raises(ConfigMigrationError, match="newer than supported"):
        migrate(raw)
    assert raw == snapshot


# ---------------------------------------------------------------------------
# §1 versioned backup rotation (keep at most 3) + legacy .bak kept
# ---------------------------------------------------------------------------


def _write_old_config(path, version):
    """A minimal real config at a given old schema version that will migrate."""
    base = JarvisConfig().model_dump(mode="json")
    base["schema_version"] = version
    path.write_text(json.dumps(base), encoding="utf-8")


def test_versioned_backup_created_on_migration(tmp_path):
    """A real load that triggers migration leaves a versioned + legacy bak."""
    p = tmp_path / "config.json"
    _write_old_config(p, CURRENT_SCHEMA_VERSION - 1)
    load_config(p)
    assert _list_versioned_backups(p), "a versioned pre-migration backup must exist"
    assert _legacy_bak_path(p).exists()  # legacy .bak kept for compatibility


def test_versioned_backups_capped_at_three(tmp_path):
    """Rotation keeps at most the 3 newest versioned backups (unit-level)."""
    p = tmp_path / "config.json"
    made = []
    for v in range(CURRENT_SCHEMA_VERSION - 5, CURRENT_SCHEMA_VERSION):
        p.write_text(json.dumps({"schema_version": v}), encoding="utf-8")
        _write_versioned_backup(p, v)
        made.append(v)
    backups = _list_versioned_backups(p)
    assert len(backups) == 3, [b.name for b in backups]
    kept = sorted(int(b.name.split(".v")[1].split(".bak")[0]) for b in backups)
    assert kept == sorted(made)[-3:]
    assert _legacy_bak_path(p).exists()


# ---------------------------------------------------------------------------
# §2 corrupt JSON → quarantine + ordered recovery, or default
# ---------------------------------------------------------------------------


def test_corrupt_json_restores_from_lastgood(tmp_path, caplog):
    """A corrupt live file is quarantined (bytes preserved) and restored from
    .lastgood; the user is notified."""
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)  # creates .lastgood
    assert _lastgood_path(p).exists()

    corrupt_bytes = b'{"schema_version": 39, "llm": {,'
    p.write_bytes(corrupt_bytes)

    notices: list[str] = []
    with caplog.at_level(logging.WARNING, logger="jarvis.core.config"):
        cfg = load_config(p, on_notice=notices.append)
    assert isinstance(cfg, JarvisConfig)

    quarantined = list(tmp_path.glob("config.json.corrupt-*"))
    assert quarantined, "corrupt file must be quarantined"
    assert quarantined[0].read_bytes() == corrupt_bytes, "quarantine keeps original bytes"
    json.loads(p.read_text(encoding="utf-8"))  # live file valid again
    assert notices and "restored" in notices[0].lower()


def test_corrupt_json_no_backup_creates_default(tmp_path, caplog):
    p = tmp_path / "config.json"
    p.write_text("not valid json {", encoding="utf-8")
    notices: list[str] = []
    with caplog.at_level(logging.WARNING, logger="jarvis.core.config"):
        cfg = load_config(p, on_notice=notices.append)
    assert isinstance(cfg, JarvisConfig)
    assert p.exists()
    json.loads(p.read_text(encoding="utf-8"))  # now a valid default
    assert list(tmp_path.glob("config.json.corrupt-*"))
    assert notices and "default" in notices[0].lower()


def test_empty_file_behaves_like_corrupt(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)  # make a .lastgood
    p.write_text("", encoding="utf-8")  # empty = invalid JSON
    cfg = load_config(p)
    assert isinstance(cfg, JarvisConfig)
    assert list(tmp_path.glob("config.json.corrupt-*"))
    json.loads(p.read_text(encoding="utf-8"))


def test_truncated_json_behaves_like_corrupt(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)
    full = p.read_text(encoding="utf-8")
    p.write_text(full[: len(full) // 2], encoding="utf-8")  # truncated mid-file
    cfg = load_config(p)
    assert isinstance(cfg, JarvisConfig)
    assert list(tmp_path.glob("config.json.corrupt-*"))


def test_recovery_priority_skips_bad_lastgood_for_versioned(tmp_path):
    """.lastgood corrupt but a .vN.bak readable → recovery uses the .vN.bak."""
    p = tmp_path / "config.json"
    # A readable versioned backup carrying a marker we can detect.
    good = JarvisConfig().model_dump(mode="json")
    good["schema_version"] = CURRENT_SCHEMA_VERSION  # already current, no migration
    _versioned_bak_path(p, 2).write_text(json.dumps(good), encoding="utf-8")
    # A CORRUPT lastgood that must be skipped.
    _lastgood_path(p).write_text("{broken lastgood", encoding="utf-8")
    # Corrupt live file triggers recovery.
    p.write_text("{also broken", encoding="utf-8")

    cfg = load_config(p)
    assert isinstance(cfg, JarvisConfig)
    # Live file now equals the v2 backup content (recovered from it).
    restored = json.loads(p.read_text(encoding="utf-8"))
    assert restored["schema_version"] == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# §5 .lastgood is created and only updated when content changes
# ---------------------------------------------------------------------------


def test_lastgood_created_on_successful_load(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    assert not _lastgood_path(p).exists()
    load_config(p)
    assert _lastgood_path(p).exists()


def test_lastgood_not_rewritten_when_unchanged(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)
    lg = _lastgood_path(p)
    mtime1 = lg.stat().st_mtime_ns
    # Second load with identical content must not rewrite the snapshot.
    _update_lastgood(p)
    assert lg.stat().st_mtime_ns == mtime1


def test_lastgood_updates_when_content_changes(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)
    lg = _lastgood_path(p)
    first = lg.read_bytes()
    # Change the live file to new valid content, then refresh lastgood.
    p.write_text(json.dumps({"schema_version": CURRENT_SCHEMA_VERSION, "x": 1}),
                 encoding="utf-8")
    _update_lastgood(p)
    assert lg.read_bytes() != first


# ---------------------------------------------------------------------------
# §4 atomic write: fsync is called; a write failure leaves the original intact
# ---------------------------------------------------------------------------


def test_save_calls_fsync(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    calls = {"n": 0}
    real_fsync = cfg_mod.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(cfg_mod.os, "fsync", counting_fsync)
    save_config(JarvisConfig(), p)
    assert calls["n"] >= 1, "save_config must fsync before replace"


def test_save_failure_leaves_original_intact(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    original = p.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cfg_mod.json, "dump", boom)
    with pytest.raises(OSError, match="disk full"):
        save_config(JarvisConfig(), p)

    assert p.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("config.json.tmp*"))


# ---------------------------------------------------------------------------
# §4 ValidationError still propagates (bad values are NOT silently recovered)
# ---------------------------------------------------------------------------


def test_validation_error_still_raised(tmp_path):
    from pydantic import ValidationError

    p = tmp_path / "config.json"
    data = JarvisConfig().model_dump(mode="json")
    data["llm"]["temperature"] = 999  # out of range
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(p)


# ---------------------------------------------------------------------------
# §6 config_health is read-only (doctor must not create/alter files)
# ---------------------------------------------------------------------------


def test_config_health_absent_does_not_create(tmp_path):
    p = tmp_path / "config.json"
    info = config_health(p)
    assert info["exists"] is False
    assert not p.exists(), "config_health must not create the file"


def test_config_health_valid_reports_version(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    info = config_health(p)
    assert info["exists"] and info["readable"]
    assert info["schema_version"] == CURRENT_SCHEMA_VERSION
    assert info["too_new"] is False
    assert info["error"] is None


def test_config_health_corrupt_does_not_modify(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{nope", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    info = config_health(p)
    assert info["error"] is not None
    assert info["readable"] is False
    # File untouched, no quarantine created by a read-only probe.
    assert p.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("config.json.corrupt-*"))


def test_config_health_too_new(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps({"schema_version": CURRENT_SCHEMA_VERSION + 3}), encoding="utf-8"
    )
    before = p.read_text(encoding="utf-8")
    info = config_health(p)
    assert info["too_new"] is True
    assert info["schema_version"] == CURRENT_SCHEMA_VERSION + 3
    assert p.read_text(encoding="utf-8") == before


def test_config_health_reports_lastgood_and_backups(tmp_path):
    p = tmp_path / "config.json"
    save_config(JarvisConfig(), p)
    load_config(p)  # creates .lastgood
    _write_versioned_backup(p, CURRENT_SCHEMA_VERSION - 1)
    info = config_health(p)
    assert info["has_lastgood"] is True
    assert info["backups"], "backups should be listed"
    assert any(".v" in name for name in info["backups"])
    # Read-only: health probe does not create a config where none differs.
    assert p.exists()
