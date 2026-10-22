"""Singleton instance lock — prevents multiple Jarvis processes (T1.5).

Uses a platform lock file at ``%APPDATA%/Jarvis/jarvis.lock`` (Windows) or
``~/.jarvis/jarvis.lock`` (Linux/macOS).  On Windows, ``msvcrt.locking`` is
used for a true advisory file lock; on POSIX, ``fcntl.flock`` is used.

Usage::

    from jarvis.core.instance_lock import acquire_instance_lock, release_instance_lock

    lock = acquire_instance_lock()
    if lock is None:
        # Another instance holds the lock.
        show_message("JARVIS is already running. Check the system tray.")
        sys.exit(1)
    try:
        ...  # run the app
    finally:
        release_instance_lock(lock)

The lock is automatically released when the process exits (OS closes the
file descriptor), so even a crash or SIGKILL won't leave a stale lock.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)


def _lock_path() -> Path:
    """Return the canonical lock-file path."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path.home() / ".jarvis"
    lock_dir = base / "Jarvis"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / "jarvis.lock"


def acquire_instance_lock() -> IO[str] | None:
    """Try to acquire an exclusive lock.

    Returns the open file handle on success (caller MUST keep it alive
    for the duration of the process and pass it to
    :func:`release_instance_lock` on shutdown).

    Returns ``None`` if another Jarvis instance already holds the lock.
    """
    path = _lock_path()
    try:
        # Open (or create) the lock file in write mode.  We keep the
        # handle open — the OS releases the lock when the fd is closed
        # (including on abnormal exit / SIGKILL).
        fh = open(path, "w", encoding="utf-8")  # noqa: SIM115

        if sys.platform == "win32":
            import msvcrt

            # Lock the first byte exclusively (non-blocking).
            # LK_NBLCK raises OSError if already locked.
            fh.write(str(os.getpid()))
            fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            # LOCK_EX | LOCK_NB → exclusive, non-blocking.
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.write(str(os.getpid()))
            fh.flush()

        log.debug("instance lock acquired: %s (pid %d)", path, os.getpid())
        return fh

    except OSError:
        # Lock held by another process → clean up the fd we just opened.
        try:
            fh.close()  # type: ignore[possibly-undefined]
        except Exception:
            log.debug("instance lock: fd close failed during cleanup", exc_info=True)
        log.info("instance lock already held by another process: %s", path)
        return None

    except Exception:
        log.warning("instance lock failed unexpectedly", exc_info=True)
        # Let the app start anyway — a missing lock is better than refusing
        # to launch because of an unexpected platform quirk.
        try:
            fh.close()  # type: ignore[possibly-undefined]
        except Exception:
            log.debug("instance lock: fd close failed during cleanup", exc_info=True)
        return None


def release_instance_lock(fh: IO[str] | None) -> None:
    """Release and clean up the lock file.

    Safe to call with ``None`` (no-op).
    """
    if fh is None:
        return
    path = _lock_path()
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # already unlocked or file truncated
        fh.close()
        # Remove the file so a stale lock file doesn't confuse users
        # poking around %APPDATA%.  If removal fails (another process
        # already re-created it) that's fine.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        log.debug("instance lock released: %s", path)
    except Exception:
        log.warning("instance lock release failed", exc_info=True)
