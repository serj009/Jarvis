"""One-time migration of Jarvis data from the legacy ``~/.jarvis`` root.

Historically several modules wrote directly to ``~/.jarvis`` on Windows
(instead of ``%APPDATA%\\Jarvis``). After Task 4+B every data module routes
through :func:`jarvis.paths.user_data_dir`, so on Windows the canonical root
is now ``%APPDATA%\\Jarvis``. Without migration those modules would no longer
see their old data.

This module copies everything from ``~/.jarvis`` into the new data root, once,
on first run. Design constraints (see Task 4+B):

* **Windows only.** On other platforms the legacy root IS the data root, so
  there is nothing to migrate.
* **Copy, never move or delete.** The legacy tree is left untouched.
* **Never overwrite.** A file already present in the new root wins; a
  *different* legacy file with the same relative path is logged as a conflict
  and skipped.
* **SQLite-aware.** ``.db`` files are copied via the sqlite3 backup API so a
  live/WAL database is captured consistently; the sidecar ``-wal`` / ``-shm``
  files are intentionally NOT copied (the backup already folds them in).
* **Skip the Piper voices dir** — it is large and ships with the install, not
  user data.
* **Idempotent.** A ``.migrated_from_dotjarvis`` marker in the new root short-
  circuits subsequent runs. The marker is written only when *no* file failed
  to copy; if any copy failed, the marker is withheld so the next run retries
  the missing files (already-copied files are skipped as existing).
* **Best-effort.** Any per-file failure is logged (with its relative path) and
  counted; the migration never raises into the boot path.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_MARKER_NAME = ".migrated_from_dotjarvis"

# Relative sub-paths under ~/.jarvis that must NOT be copied (large assets /
# install resources, not user data).
_SKIP_DIRS = ("voices",)


def _legacy_root() -> Path:
    return Path.home() / ".jarvis"


def _is_sqlite(path: Path) -> bool:
    return path.suffix.lower() == ".db"


def _is_sqlite_sidecar(path: Path) -> bool:
    # WAL/SHM are folded into the backup copy, so skip them explicitly.
    return path.suffix.lower() in (".db-wal", ".db-shm") or path.name.endswith(
        ("-wal", "-shm")
    )


def _copy_sqlite(src: Path, dst: Path) -> bool:
    """Copy a SQLite DB via the backup API (consistent even if WAL-active).

    Returns True on success. Verifies the copy with a quick integrity check.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        src_conn.backup(dst_conn)
        # Integrity check on the fresh copy.
        row = dst_conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            log.warning("migration: integrity check failed for %s; removing copy", dst)
            dst_conn.close()
            dst_conn = None
            dst.unlink(missing_ok=True)
            return False
        return True
    except Exception:
        log.warning("migration: sqlite backup failed for %s", src, exc_info=True)
        if dst_conn is not None:
            try:
                dst_conn.close()
                dst_conn = None
            except Exception:
                pass
            dst.unlink(missing_ok=True)
        return False
    finally:
        for c in (src_conn, dst_conn):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass


def _copy_plain(src: Path, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception:
        log.warning("migration: copy failed for %s", src, exc_info=True)
        return False


def migrate_from_dotjarvis() -> bool:
    """Run the one-time copy from ~/.jarvis to the current data root.

    Returns True if a migration pass ran (or was already done), False when it
    does not apply (non-Windows, no legacy dir, or data root == legacy root).
    Never raises.
    """
    if sys.platform != "win32":
        return False

    # Imported here (not at module top) so the data root is resolved at call
    # time and respects JARVIS_DATA_DIR in tests.
    from jarvis.paths import user_data_dir

    try:
        legacy = _legacy_root().resolve()
        new_root = user_data_dir().resolve()
    except OSError:
        return False

    if not legacy.is_dir() or legacy == new_root:
        return False

    marker = new_root / _MARKER_NAME
    if marker.exists():
        return True  # already migrated

    copied = skipped = conflicts = failed = 0
    failed_paths: list[str] = []
    for src in legacy.rglob("*"):
        if src.is_dir():
            continue
        try:
            rel = src.relative_to(legacy)
        except ValueError:
            continue
        # Skip large asset dirs (Piper voices) and SQLite sidecars.
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        if _is_sqlite_sidecar(src):
            continue

        dst = new_root / rel
        if dst.exists():
            # Never overwrite. If contents differ, record a conflict.
            try:
                if _is_sqlite(src) or src.stat().st_size != dst.stat().st_size:
                    conflicts += 1
                    log.warning(
                        "migration: conflict, keeping new file: %s", rel,
                    )
            except OSError:
                pass
            skipped += 1
            continue

        ok = _copy_sqlite(src, dst) if _is_sqlite(src) else _copy_plain(src, dst)
        if ok:
            copied += 1
        else:
            failed += 1
            failed_paths.append(str(rel))

    # Only mark the migration done when nothing failed to copy. If some files
    # failed, leave the marker absent so the next run retries them (already
    # copied files are skipped as existing), and warn with the paths.
    if failed == 0:
        try:
            new_root.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                "Migrated from ~/.jarvis. Safe to delete; presence only prevents "
                "re-running the one-time migration.\n",
                encoding="utf-8",
            )
        except OSError:
            log.debug("migration: could not write marker", exc_info=True)
    else:
        log.warning(
            "migration from ~/.jarvis: %d file(s) failed to copy, marker not "
            "written (will retry next run): %s",
            failed, ", ".join(failed_paths),
        )

    log.info(
        "migration from ~/.jarvis complete: %d copied, %d skipped, "
        "%d conflicts, %d failed",
        copied, skipped, conflicts, failed,
    )
    return True
