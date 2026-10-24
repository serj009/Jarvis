"""Task H: atomic file writes (jarvis.core.fileio).

Pure stdlib — not auto-skipped in a lightweight venv. All tests write under
tmp_path, so nothing touches real data.
"""

from __future__ import annotations

import json
import os

import pytest

from jarvis.core import fileio
from jarvis.core.fileio import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


def _temps(dir_path):
    return [p for p in dir_path.iterdir() if ".tmp" in p.name]


# ---------------------------------------------------------------------------
# success + parent creation
# ---------------------------------------------------------------------------


def test_write_text_creates_parent_and_file(tmp_path):
    p = tmp_path / "sub" / "deeper" / "a.txt"
    atomic_write_text(p, "héllo")
    assert p.read_text(encoding="utf-8") == "héllo"
    assert _temps(p.parent) == []


def test_write_bytes_roundtrip(tmp_path):
    p = tmp_path / "b.bin"
    atomic_write_bytes(p, b"\x00\x01\x02")
    assert p.read_bytes() == b"\x00\x01\x02"


def test_write_json_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    atomic_write_json(p, {"x": 1, " y": "ü"}, ensure_ascii=False, indent=2)
    assert json.loads(p.read_text(encoding="utf-8")) == {"x": 1, " y": "ü"}


# ---------------------------------------------------------------------------
# fsync is called
# ---------------------------------------------------------------------------


def test_fsync_is_called(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = os.fsync

    def counting(fd):
        calls["n"] += 1
        return real(fd)

    monkeypatch.setattr(fileio.os, "fsync", counting)
    atomic_write_text(tmp_path / "d.txt", "data")
    assert calls["n"] >= 1


# ---------------------------------------------------------------------------
# failure mid-write leaves original intact + no temp debris
# ---------------------------------------------------------------------------


def test_replace_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    p = tmp_path / "e.txt"
    atomic_write_text(p, "ORIGINAL")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(p, "NEW")

    assert p.read_text(encoding="utf-8") == "ORIGINAL"
    assert _temps(tmp_path) == [], "temp file must be cleaned up on failure"


def test_fsync_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"
    atomic_write_text(p, "ORIGINAL")

    def boom(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(fileio.os, "fsync", boom)
    with pytest.raises(OSError, match="fsync failed"):
        atomic_write_text(p, "NEW")

    assert p.read_text(encoding="utf-8") == "ORIGINAL"
    assert _temps(tmp_path) == []


def test_missing_target_not_created_on_failure(tmp_path, monkeypatch):
    """If the write fails and there was no original, no file is left behind."""
    p = tmp_path / "ghost.txt"

    def boom(src, dst):
        raise OSError("nope")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "data")
    assert not p.exists()
    assert _temps(tmp_path) == []


def test_json_serialisation_error_writes_nothing(tmp_path):
    p = tmp_path / "g.json"

    class NotSerialisable:
        pass

    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": NotSerialisable()})
    assert not p.exists()
    assert _temps(tmp_path) == []


# ---------------------------------------------------------------------------
# concurrent writers to the same path do not corrupt each other
# ---------------------------------------------------------------------------


def test_concurrent_writes_do_not_corrupt(tmp_path):
    import threading

    p = tmp_path / "shared.json"
    errors: list[BaseException] = []

    def writer(n: int):
        try:
            for _ in range(20):
                atomic_write_json(p, {"writer": n, "payload": list(range(50))})
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # The final file must be whole, valid JSON written by exactly one writer
    # (never a torn blend of two), and no temp files may linger.
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["writer"] in range(6)
    assert data["payload"] == list(range(50))
    assert _temps(tmp_path) == []


def test_unique_temp_names():
    from pathlib import Path

    a = fileio._tmp_path(Path("/x/config.json"))
    b = fileio._tmp_path(Path("/x/config.json"))
    assert a != b
    assert a.parent == Path("/x")  # same directory as target


# ---------------------------------------------------------------------------
# former direct writers: a write error leaves the original intact
# ---------------------------------------------------------------------------


def test_intent_cache_save_error_keeps_original(tmp_path, monkeypatch):
    """IntentCache.save now routes through atomic_write_json; a write failure
    must be swallowed (log.warning) AND leave the prior file intact."""
    from jarvis.memory.cache import IntentCache

    p = tmp_path / "intent_cache.json"
    c = IntentCache(path=p)
    c.learn("open chrome", "open_app")
    c.save()
    original = p.read_text(encoding="utf-8")

    monkeypatch.setattr(
        fileio.os, "replace",
        lambda s, d: (_ for _ in ()).throw(OSError("boom")),
    )
    c.learn("another query", "other_tool")
    c.save()  # must NOT raise (IntentCache catches + logs)

    assert p.read_text(encoding="utf-8") == original
    assert _temps(tmp_path) == []
