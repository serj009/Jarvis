"""ConversationMemory: persistent conversation logging with rotation (T4.2).

Stores complete conversation turns (user + assistant) in a SQLite database,
rotated by quarter (Q1-Q4). The current quarter's conversations are in
the active database; older quarters are archived and searchable.

Data path: %APPDATA%/Jarvis/memory/conversations/current.db
           %APPDATA%/Jarvis/memory/conversations/archive/2026_Q4.db

Usage:
    conv_mem = ConversationMemory()
    await conv_mem.open()
    await conv_mem.log_turn("What's the weather?", "It's 15 degrees, sir.")
    turns = await conv_mem.recent(limit=10)
    results = await conv_mem.search("weather")
    await conv_mem.close()
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ConversationTurn:
    """One user→assistant exchange."""

    id: str
    user_text: str
    assistant_text: str
    language: str
    created_at: str
    session_id: str = ""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _conversations_root() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis" / "memory" / "conversations"
    return Path.home() / ".jarvis" / "memory" / "conversations"


def _current_quarter() -> str:
    """Return e.g. '2026_Q4'."""
    now = datetime.now(UTC)
    quarter = (now.month - 1) // 3 + 1
    return f"{now.year}_Q{quarter}"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    user_text       TEXT NOT NULL,
    assistant_text  TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT 'en',
    created_at      TEXT NOT NULL,
    session_id      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_turns_created ON turns(created_at);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
"""


# ---------------------------------------------------------------------------
# ConversationMemory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """Persistent conversation logging with quarterly rotation."""

    def __init__(self, root_path: Path | None = None) -> None:
        self._root = root_path or _conversations_root()
        self._current_quarter = _current_quarter()
        self._session_id = uuid4().hex[:8]
        self._opened = False

    @property
    def is_open(self) -> bool:
        return self._opened

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- lifecycle ----------------------------------------------------------

    async def open(self) -> None:
        if self._opened:
            return
        await asyncio.to_thread(self._sync_open)
        self._opened = True
        log.info("conversation memory opened (session=%s, quarter=%s)",
                 self._session_id, self._current_quarter)

    def _sync_open(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "archive").mkdir(exist_ok=True)
        # Check if we need to rotate
        self._maybe_rotate()
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.close()

    async def close(self) -> None:
        self._opened = False

    # -- logging ------------------------------------------------------------

    async def log_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        language: str = "en",
    ) -> str:
        """Log a conversation turn. Returns turn ID."""
        turn_id = uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            self._sync_log, turn_id, user_text, assistant_text,
            language, now,
        )
        return turn_id

    def _sync_log(
        self, turn_id: str, user_text: str, assistant_text: str,
        language: str, now: str,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO turns (id, user_text, assistant_text, language, "
                "created_at, session_id) VALUES (?, ?, ?, ?, ?, ?)",
                (turn_id, user_text, assistant_text, language, now, self._session_id),
            )
            conn.commit()
        finally:
            conn.close()

    # -- queries ------------------------------------------------------------

    async def recent(self, limit: int = 20) -> list[ConversationTurn]:
        """Get most recent turns."""
        return await asyncio.to_thread(self._sync_recent, limit)

    def _sync_recent(self, limit: int) -> list[ConversationTurn]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM turns ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_turn(r) for r in reversed(rows)]
        finally:
            conn.close()

    async def search(self, query: str, *, limit: int = 10) -> list[ConversationTurn]:
        """Search conversations by text (LIKE match on user or assistant text)."""
        return await asyncio.to_thread(self._sync_search, query, limit)

    def _sync_search(self, query: str, limit: int) -> list[ConversationTurn]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM turns WHERE user_text LIKE ? OR assistant_text LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [self._row_to_turn(r) for r in rows]
        finally:
            conn.close()

    async def count(self) -> int:
        """Total number of turns in current database."""
        return await asyncio.to_thread(self._sync_count)

    def _sync_count(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # -- rotation -----------------------------------------------------------

    def _maybe_rotate(self) -> None:
        """If the quarter changed, move current.db to archive."""
        current_db = self._root / "current.db"
        if not current_db.exists():
            return
        # Check the quarter of the last entry
        conn = sqlite3.connect(str(current_db))
        try:
            row = conn.execute(
                "SELECT created_at FROM turns ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            conn.close()
            return
        conn.close()

        if row is None:
            return

        last_date = row[0][:10]  # "2026-09-15"
        try:
            dt = datetime.fromisoformat(last_date)
            last_q = (dt.month - 1) // 3 + 1
            last_quarter = f"{dt.year}_Q{last_q}"
        except ValueError:
            return

        if last_quarter != self._current_quarter:
            archive_path = self._root / "archive" / f"{last_quarter}.db"
            if not archive_path.exists():
                import shutil
                shutil.move(str(current_db), str(archive_path))
                log.info("rotated conversations: %s -> %s", current_db.name, archive_path.name)

    # -- helpers ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        db_path = self._root / "current.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _row_to_turn(row: tuple) -> ConversationTurn:
        return ConversationTurn(
            id=row[0],
            user_text=row[1],
            assistant_text=row[2],
            language=row[3],
            created_at=row[4],
            session_id=row[5] if len(row) > 5 else "",
        )
