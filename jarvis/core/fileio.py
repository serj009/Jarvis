"""Atomic file writes (Task H).

A single, reusable implementation of the crash-safe write pattern already
proven in ``jarvis.core.config.save_config``: serialise into a uniquely-named
temp file in the SAME directory, ``flush`` + ``os.fsync`` it, then
``os.replace`` onto the destination (atomic on the same filesystem). On any
failure the partial temp file is removed and the original destination is left
untouched; the exception propagates so the caller decides whether to swallow
it (most stores log a warning and continue).

Why a unique temp name (PID + random): two processes — or two threads — saving
the same path must not scribble over each other's in-flight temp file. The
final ``os.replace`` is still atomic, so the last writer wins cleanly without
either observer ever seeing a truncated file.

Use these instead of hand-rolling ``tmp + replace`` (which usually forgets the
fsync, so a power loss right after replace can still surface a zero-length
file on some filesystems).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


def _tmp_path(path: Path) -> Path:
    """A unique sibling temp path: ``<name>.tmp<pid>-<rand>``.

    Same directory as *path* so ``os.replace`` stays on one filesystem.
    """
    suffix = f".tmp{os.getpid()}-{secrets.token_hex(4)}"
    return path.with_name(path.name + suffix)


def _replace_with_retry(src: Path, dst: Path, attempts: int = 10) -> None:
    """``os.replace`` with a short retry on Windows ``PermissionError``.

    On Windows, ``os.replace`` onto a destination another thread/process is
    simultaneously replacing (or a scanner/indexer briefly holds) raises
    ``PermissionError``/ERROR_ACCESS_DENIED even though each temp file is
    itself intact. A few short retries let the transient lock clear; the final
    replace is still atomic, so a reader never sees a torn file.
    """
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Atomically write *data* to *path*.

    Creates the parent directory, writes to a unique temp file, fsyncs it, and
    replaces the destination. Removes the temp file on any error and re-raises;
    the original *path* is never left partially written.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(p)
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, p)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(
    path: str | os.PathLike[str], text: str, encoding: str = "utf-8"
) -> None:
    """Atomically write *text* to *path* (see :func:`atomic_write_bytes`)."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(
    path: str | os.PathLike[str], obj: Any, **dumps_kwargs: Any
) -> None:
    """Atomically write *obj* as JSON to *path*.

    ``dumps_kwargs`` are forwarded to ``json.dumps`` (e.g. ``indent=2``,
    ``ensure_ascii=False``). Serialisation happens BEFORE any file is touched,
    so a non-serialisable object raises without creating a temp file.
    """
    text = json.dumps(obj, **dumps_kwargs)
    atomic_write_text(path, text)
