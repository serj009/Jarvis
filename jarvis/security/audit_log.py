"""Layer 4 — Security Audit Log: tamper-evident, encrypted audit trail.

Records ALL security-relevant events in an encrypted, append-only SQLite
database.  Each entry is chained via SHA-256 (hash of previous entry +
current payload), making tampering detectable.

Events logged
-------------
- Every tool call (tool_name, args summary, origin, result).
- Every blocked action (reason, what was blocked).
- Every data-leak warning/block.
- Every sanitizer trigger (what pattern matched).
- LLM responses that triggered the sanitizer.
- Session start/stop boundaries.

Storage
-------
- Separate SQLite DB: ``~/.jarvis/security_audit.db`` (NOT memory.db).
- ``data`` column encrypted with DPAPI column-level encryption
  (``jarvis.platform.encrypted_db``).
- Hash chain: ``entry_hash = SHA256(prev_hash + entry_json)``.
- ``verify_chain()`` walks the entire chain and checks every link.

Thread safety
-------------
All writes go through ``_append()`` which serialises via a threading Lock.
Reads (``get_recent``, ``verify_chain``) acquire a shared snapshot from
SQLite's WAL mode and do not block writes.

Non-Windows
-----------
On non-Windows platforms, DPAPI is unavailable and the data column is
stored in plaintext (same documented fallback as ``encrypted_db.py`` and
``secrets.py``).  The hash chain remains intact regardless of platform.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Literal

from jarvis.platform.encrypted_db import decrypt_value, encrypt_value

log = logging.getLogger(__name__)

# Genesis hash — the prev_hash of the very first entry.
_GENESIS_HASH = "0" * 64

# Event types stored in the ``event_type`` column.
EventType = Literal[
    "tool_call",
    "blocked",
    "leak_warning",
    "sanitizer",
    "session",
]

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hash       TEXT    NOT NULL,
    prev_hash  TEXT    NOT NULL,
    event_type TEXT    NOT NULL,
    data       TEXT    NOT NULL,
    timestamp  REAL    NOT NULL
);
"""


class SecurityAuditLog:
    """Tamper-evident security audit log.

    Construct with an optional ``db_path``; defaults to
    ``~/.jarvis/security_audit.db``.  The database and its parent
    directories are created lazily on the first write.

    Public API
    ----------
    log_tool_call      — record a tool execution.
    log_blocked        — record a blocked action.
    log_leak_warning   — record a data-leak detection.
    log_sanitizer_trigger — record a sanitizer match.
    log_session        — record session start/stop.
    get_recent         — retrieve recent audit entries (decrypted).
    verify_chain       — verify the hash chain hasn't been tampered with.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        # Single data-dir resolver (jarvis.paths); layout unchanged:
        # <data_dir>/security_audit.db. Resolved at call time.
        from jarvis.paths import data_path
        self._db_path = db_path if db_path is not None else data_path("security_audit.db")
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._prev_hash: str = _GENESIS_HASH
        self._init_db()

    # ------------------------------------------------------------------
    # Database lifecycle
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the schema and load the last hash from the chain."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()
        self._prev_hash = self._get_last_hash()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _get_last_hash(self) -> str:
        """Retrieve the hash of the most recent entry (or genesis)."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1",
        ).fetchone()
        return row[0] if row else _GENESIS_HASH

    def close(self) -> None:
        """Explicitly close the DB connection (for clean shutdown)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    log.debug("audit_log: close failed", exc_info=True)
                self._conn = None

    # ------------------------------------------------------------------
    # Core append — all public log_* methods funnel through here
    # ------------------------------------------------------------------

    def _append(self, event_type: str, data: dict) -> str:
        """Append an entry with a chained hash.

        Returns the entry's hash.  The caller never needs it, but
        returning it simplifies testing.
        """
        ts = time.time()
        entry_payload = json.dumps(
            {"type": event_type, "data": data, "ts": ts},
            ensure_ascii=False,
            sort_keys=True,
        )

        with self._lock:
            entry_hash = hashlib.sha256(
                (self._prev_hash + entry_payload).encode("utf-8"),
            ).hexdigest()

            encrypted_data = encrypt_value(entry_payload)

            conn = self._get_conn()
            conn.execute(
                "INSERT INTO audit_log (hash, prev_hash, event_type, data, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry_hash, self._prev_hash, event_type, encrypted_data, ts),
            )
            conn.commit()

            self._prev_hash = entry_hash

        return entry_hash

    # ------------------------------------------------------------------
    # Public logging helpers
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        tool_name: str,
        args_summary: str,
        origin: str,
        result_summary: str,
    ) -> None:
        """Log a tool execution event."""
        self._append("tool_call", {
            "tool_name": tool_name,
            "args_summary": args_summary,
            "origin": origin,
            "result_summary": result_summary,
        })

    def log_blocked(
        self,
        tool_name: str,
        reason: str,
        details: str,
    ) -> None:
        """Log a blocked action."""
        self._append("blocked", {
            "tool_name": tool_name,
            "reason": reason,
            "details": details,
        })

    def log_leak_warning(
        self,
        url: str,
        matched_data: str,
        origin: str,
    ) -> None:
        """Log a data-leak detection event."""
        self._append("leak_warning", {
            "url": url,
            "matched_data": matched_data,
            "origin": origin,
        })

    def log_sanitizer_trigger(
        self,
        pattern_name: str,
        content_preview: str,
        source: str,
    ) -> None:
        """Log a sanitizer pattern match."""
        self._append("sanitizer", {
            "pattern_name": pattern_name,
            "content_preview": content_preview[:200],
            "source": source,
        })

    def log_session(self, event: Literal["start", "stop"]) -> None:
        """Log a session boundary event."""
        self._append("session", {"event": event})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent(self, n: int = 50) -> list[dict]:
        """Return the *n* most recent audit entries, decrypted.

        Each dict has: ``id``, ``hash``, ``prev_hash``, ``event_type``,
        ``data`` (parsed dict), ``timestamp``.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, hash, prev_hash, event_type, data, timestamp "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()

        entries: list[dict] = []
        for row in rows:
            raw_data = decrypt_value(row[4]) or "{}"
            try:
                parsed = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                parsed = {"_raw": raw_data}
            entries.append({
                "id": row[0],
                "hash": row[1],
                "prev_hash": row[2],
                "event_type": row[3],
                "data": parsed,
                "timestamp": row[5],
            })

        return entries

    # ------------------------------------------------------------------
    # Chain verification
    # ------------------------------------------------------------------

    def verify_chain(self) -> bool:
        """Walk the full hash chain and verify every link.

        Returns True if the chain is intact (including an empty log).
        Returns False if any entry's hash does not match the expected
        ``SHA256(prev_hash + decrypted_payload)``.

        This method is intentionally synchronous and reads the entire
        table — call it during scheduled audits, not on the hot path.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, hash, prev_hash, data, timestamp "
            "FROM audit_log ORDER BY id ASC",
        ).fetchall()

        if not rows:
            return True  # empty log is trivially valid

        expected_prev = _GENESIS_HASH

        for row in rows:
            entry_id, stored_hash, stored_prev, encrypted_data, _ts = row

            # Prev-hash must link to the previous entry's hash.
            if stored_prev != expected_prev:
                log.error(
                    "[audit-log] chain break at id=%d: prev_hash mismatch "
                    "(stored=%s, expected=%s)",
                    entry_id, stored_prev[:16], expected_prev[:16],
                )
                return False

            # Decrypt the payload and recompute the hash.
            decrypted = decrypt_value(encrypted_data) or ""
            recomputed = hashlib.sha256(
                (stored_prev + decrypted).encode("utf-8"),
            ).hexdigest()

            if recomputed != stored_hash:
                log.error(
                    "[audit-log] chain break at id=%d: hash mismatch "
                    "(stored=%s, recomputed=%s)",
                    entry_id, stored_hash[:16], recomputed[:16],
                )
                return False

            expected_prev = stored_hash

        return True
