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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _default_db_path() -> Path:
    # Resolved at call time via the single data-dir resolver (jarvis.paths);
    # layout unchanged: <data_dir>/scheduled_reminders.db.
    from jarvis.paths import data_path

    return data_path("scheduled_reminders.db")

_CHECK_INTERVAL_SECONDS = 30

_VALID_RECURRENCE = {"daily", "weekdays", "weekly", "monthly"}


def compute_next_trigger(previous: datetime, recurrence: str) -> datetime:
    """Return the next trigger time after *previous* for a recurrence rule.

    Keeps the same clock time (hour/minute) as *previous*.
      daily     -> +1 day
      weekdays  -> next Mon–Fri day (skip Sat/Sun)
      weekly    -> +7 days (same weekday)
      monthly   -> same day-of-month next month; days >28 clamp to the
                   month's last day.
    Unknown rule -> +1 day (safe default).
    """
    if recurrence == "weekly":
        return previous + timedelta(days=7)
    if recurrence == "monthly":
        # Advance one month, clamping the day to the target month's length.
        year = previous.year + (1 if previous.month == 12 else 0)
        month = 1 if previous.month == 12 else previous.month + 1
        last_day = _days_in_month(year, month)
        day = min(previous.day, last_day)
        return previous.replace(year=year, month=month, day=day)
    if recurrence == "weekdays":
        nxt = previous + timedelta(days=1)
        while nxt.weekday() >= 5:  # 5=Sat, 6=Sun
            nxt += timedelta(days=1)
        return nxt
    # daily (and safe default)
    return previous + timedelta(days=1)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScheduledReminder:
    """One scheduled reminder — immutable after creation.

    ``recurrence`` (daily / weekdays / weekly / monthly) and
    ``recurrence_time`` (HH:MM) are None for one-shot reminders. When set,
    the scheduler reschedules the reminder after it fires instead of
    leaving it done.
    """

    id: str
    text: str
    trigger_at: datetime
    created_at: datetime
    fired: bool = False
    recurrence: str | None = None
    recurrence_time: str | None = None


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
        self._db_path = db_path if db_path is not None else _default_db_path()
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
        # Migration: add recurrence columns to pre-existing databases.
        # ALTER TABLE ADD COLUMN is a no-op-safe pattern guarded by a probe.
        existing = {
            row[1]
            for row in self._conn.execute(
                "PRAGMA table_info(scheduled_reminders)"
            ).fetchall()
        }
        if "recurrence" not in existing:
            self._conn.execute(
                "ALTER TABLE scheduled_reminders ADD COLUMN recurrence TEXT"
            )
        if "recurrence_time" not in existing:
            self._conn.execute(
                "ALTER TABLE scheduled_reminders ADD COLUMN recurrence_time TEXT"
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
            recurrence=row[5] if len(row) > 5 else None,
            recurrence_time=row[6] if len(row) > 6 else None,
        )

    # -- CRUD ------------------------------------------------------------

    def add(
        self,
        text: str,
        trigger_at: datetime,
        *,
        recurrence: str | None = None,
        recurrence_time: str | None = None,
    ) -> ScheduledReminder:
        """Create and persist a new reminder.  Returns the created record.

        ``recurrence`` / ``recurrence_time`` default to None (one-shot);
        set them for a repeating reminder.
        """
        conn = self._ensure_open()
        reminder = ScheduledReminder(
            id=uuid.uuid4().hex[:12],
            text=text,
            trigger_at=trigger_at,
            created_at=datetime.now(tz=UTC),
            fired=False,
            recurrence=recurrence,
            recurrence_time=recurrence_time,
        )
        conn.execute(
            "INSERT INTO scheduled_reminders "
            "(id, text, trigger_at, created_at, fired, recurrence, recurrence_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                reminder.id,
                reminder.text,
                reminder.trigger_at.isoformat(),
                reminder.created_at.isoformat(),
                0,
                reminder.recurrence,
                reminder.recurrence_time,
            ),
        )
        conn.commit()
        log.info("[sched-remind] added id=%s trigger_at=%s recurrence=%s text=%s",
                 reminder.id, reminder.trigger_at, reminder.recurrence, reminder.text[:60])
        return reminder

    def update_reminder(self, reminder_id: str, **fields) -> bool:
        """Update selected columns of a reminder. Returns True if it existed.

        Accepts: text, trigger_at (datetime), fired (bool), recurrence,
        recurrence_time. Used by edit / snooze / recurring reschedule.
        Unknown keys are ignored.
        """
        allowed = {"text", "trigger_at", "fired", "recurrence", "recurrence_time"}
        sets: list[str] = []
        values: list = []
        for key, val in fields.items():
            if key not in allowed:
                continue
            if key == "trigger_at" and isinstance(val, datetime):
                val = val.isoformat()
            elif key == "fired":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            values.append(val)
        if not sets:
            return False
        conn = self._ensure_open()
        values.append(reminder_id)
        cur = conn.execute(
            f"UPDATE scheduled_reminders SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0

    def get(self, reminder_id: str) -> ScheduledReminder | None:
        """Fetch a single reminder by id."""
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
            "FROM scheduled_reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        return self._row_to_reminder(row) if row else None

    def last_fired(self) -> ScheduledReminder | None:
        """Most recently fired reminder (for snooze). None if none fired."""
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
            "FROM scheduled_reminders WHERE fired = 1 "
            "ORDER BY trigger_at DESC LIMIT 1",
        ).fetchone()
        return self._row_to_reminder(row) if row else None

    def get_pending(self) -> list[ScheduledReminder]:
        """Return every un-fired reminder whose trigger_at is now or in the past."""
        conn = self._ensure_open()
        now_iso = datetime.now(tz=UTC).isoformat()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
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
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
            "FROM scheduled_reminders "
            "ORDER BY trigger_at DESC",
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def list_pending(self) -> list[ScheduledReminder]:
        """Return every un-fired reminder, soonest first."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
            "FROM scheduled_reminders "
            "WHERE fired = 0 "
            "ORDER BY trigger_at",
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def find_by_text(self, fragment: str) -> list[ScheduledReminder]:
        """Find un-fired reminders whose text contains *fragment* (case-insensitive)."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT id, text, trigger_at, created_at, fired, recurrence, recurrence_time "
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
        """One pass: fire every overdue reminder.

        One-shot reminders are marked fired. Recurring reminders are
        rescheduled: their next trigger is computed and ``fired`` stays 0
        so the loop keeps firing them on cadence.
        """
        pending = self._store.get_pending()
        for reminder in pending:
            try:
                self._on_fire(reminder)
            except Exception:
                log.exception("[sched-remind] on_fire failed for %s", reminder.id)
            if reminder.recurrence in _VALID_RECURRENCE:
                next_at = compute_next_trigger(reminder.trigger_at, reminder.recurrence)
                # If we fell behind (missed ticks), roll forward past now.
                now = datetime.now(tz=UTC)
                while next_at <= now:
                    next_at = compute_next_trigger(next_at, reminder.recurrence)
                self._store.update_reminder(
                    reminder.id, trigger_at=next_at, fired=False,
                )
                log.info(
                    "[sched-remind] rescheduled recurring id=%s -> %s",
                    reminder.id, next_at,
                )
            else:
                self._store.mark_fired(reminder.id)

    def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
