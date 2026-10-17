"""Persistent time-based reminders — schedule, store, and fire.

Unlike event-triggered reminders (``reminders.py``), these fire at a
specific wall-clock time and survive a Jarvis restart.

Storage:
    SQLite table ``scheduled_reminders`` in the same ``~/.jarvis/`` data dir
    used by the memory subsystem.

Architecture:
    ScheduledReminder  — immutable data record for one reminder.
    ReminderStore      — SQLite CRUD: add / get_pending / mark_fired / delete.
    ReminderScheduler  — asyncio background loop (30 s tick); fires a callback
                         for every reminder whose ``trigger_at`` has passed.

The callback signature ``(ScheduledReminder) -> None`` is intentionally
synchronous so the caller (pipeline/app.py) can dispatch both TTS and a
Windows toast without awaiting inside the scheduler loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".jarvis" / "scheduled_reminders.db"

_CHECK_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScheduledReminder:
    """One scheduled reminder — immutable after creation."""

    id: str
    text: str
    trigger_at: datetime
    created_at: datetime
    fired: bool = False


# ---------------------------------------------------------------------------
# SQLite-backed store
# ---------------------------------------------------------------------------

class ReminderStore:
    """Persistent CRUD over an SQLite table.

    Thread-safety: sqlite3 in serialized mode (the Python default) is
    sufficient — the store is only accessed from the main event loop and
    the 30 s scheduler tick, both on the same thread.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle -------------------------------------------------------

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_reminders (
                id          TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                trigger_at  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                fired       INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    # -- helpers ---------------------------------------------------------

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ReminderStore is not open — call open() first")
        return self._conn

    @staticmethod
    def _row_to_reminder(row: tuple) -> ScheduledReminder:
        return ScheduledReminder(
            id=row[0],
            text=row[1],
            trigger_at=datetime.fromisoformat(row[2]),
            created_at=datetime.fromisoformat(row[3]),
            fired=bool(row[4]),
        )

    # -- CRUD ------------------------------------------------------------

    def add(self, text: str, trigger_at: datetime) -> ScheduledReminder:
        """Create and persist a new reminder.  Returns the created record."""
        conn = self._ensure_open()
        reminder = ScheduledReminder(
            id=uuid.uuid4().hex[:12],
            text=text,
            trigger_at=trigger_at,
            created_at=datetime.now(tz=timezone.utc),
            fired=False,
        )
        conn.execute(
            "INSERT INTO scheduled_reminders (id, text, trigger_at, created_at, fired) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                reminder.id,
                reminder.text,
                reminder.trigger_at.isoformat(),
                reminder.created_at.isoformat(),
                0,
            ),
        )
        conn.commit()
        log.info("[sched-remind] added id=%s trigger_at=%s text=%s",
                 reminder.id, reminder.trigger_at, reminder.text[:60])
        return reminder

    def get_pending(self) -> list[ScheduledReminder]:
        """Return every un-fired reminder whose trigger_at is now or in the past."""
        conn = self._ensure_open()
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired "
            "FROM scheduled_reminders "
            "WHERE fired = 0 AND trigger_at <= ? "
            "ORDER BY trigger_at",
            (now_iso,),
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def mark_fired(self, reminder_id: str) -> None:
        conn = self._ensure_open()
        conn.execute(
            "UPDATE scheduled_reminders SET fired = 1 WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()

    def delete(self, reminder_id: str) -> bool:
        """Delete a reminder by id.  Returns True if a row was removed."""
        conn = self._ensure_open()
        cur = conn.execute(
            "DELETE FROM scheduled_reminders WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    def list_all(self) -> list[ScheduledReminder]:
        """Return every reminder (pending and fired), newest first."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired "
            "FROM scheduled_reminders "
            "ORDER BY trigger_at DESC",
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def list_pending(self) -> list[ScheduledReminder]:
        """Return every un-fired reminder, soonest first."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired "
            "FROM scheduled_reminders "
            "WHERE fired = 0 "
            "ORDER BY trigger_at",
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def find_by_text(self, fragment: str) -> list[ScheduledReminder]:
        """Find un-fired reminders whose text contains *fragment* (case-insensitive)."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired "
            "FROM scheduled_reminders "
            "WHERE fired = 0 AND LOWER(text) LIKE ? "
            "ORDER BY trigger_at",
            (f"%{fragment.lower()}%",),
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

class ReminderScheduler:
    """Async background loop that fires pending reminders every 30 s.

    Usage::

        scheduler = ReminderScheduler(store, on_fire=my_callback)
        asyncio.create_task(scheduler.run())   # fire-and-forget
        ...
        scheduler.stop()                       # graceful shutdown
    """

    def __init__(
        self,
        store: ReminderStore,
        on_fire: Callable[[ScheduledReminder], None],
        *,
        check_interval: float = _CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._on_fire = on_fire
        self._check_interval = check_interval
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def run(self) -> None:
        """Infinite loop — check every *check_interval* seconds."""
        self._running = True
        log.info("[sched-remind] scheduler started (interval=%ss)", self._check_interval)
        try:
            while self._running:
                try:
                    self._tick()
                except Exception:
                    log.exception("[sched-remind] tick error")
                await asyncio.sleep(self._check_interval)
        except asyncio.CancelledError:
            log.info("[sched-remind] scheduler cancelled")
        finally:
            self._running = False

    def _tick(self) -> None:
        """One pass: fire every overdue reminder."""
        pending = self._store.get_pending()
        for reminder in pending:
            try:
                self._on_fire(reminder)
            except Exception:
                log.exception("[sched-remind] on_fire failed for %s", reminder.id)
            self._store.mark_fired(reminder.id)

    def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
