"""Time-based reminders — "remind me in 30 min to buy milk".

Persistent (SQLite-backed), survives restart.  Supports EN / RU / UK
voice patterns for:

- relative time:  "remind me in 30 minutes to buy milk"
                  "напомни через 30 минут купить молоко"
                  "нагадай через 30 хвилин купити молоко"

- absolute time:  "remind me at 15:00 to call mom"
                  "напомни в 15:00 позвонить маме"
                  "нагадай о 15:00 зателефонувати мамі"

- tomorrow:       "remind me tomorrow at 9 to check email"
                  "напомни завтра в 9:00 проверить почту"
                  "нагадай завтра о 9:00 перевірити пошту"

- list/delete:    "show reminders" / "delete reminder <text>"

Notification is done via the ``on_fire`` callback wired in ``app.py``:
TTS speaks the reminder and a Windows toast is shown.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.memory.scheduled_reminders import ReminderStore, ScheduledReminder

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton store (created lazily by tools, or injected)
# ---------------------------------------------------------------------------

_store: ReminderStore | None = None


def _get_store() -> ReminderStore:
    global _store
    if _store is None:
        _store = ReminderStore()
        _store.open()
    return _store


# ---------------------------------------------------------------------------
# Time parsing helpers
# ---------------------------------------------------------------------------

_RELATIVE_MULTIPLIERS = {
    # EN
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "h": 3600, "hr": 3600, "hrs": 3600,
    # RU
    "минут": 60, "минуту": 60, "минуты": 60, "минута": 60, "мин": 60,
    "час": 3600, "часов": 3600, "часа": 3600, "ч": 3600,
    # UK
    "хвилин": 60, "хвилину": 60, "хвилини": 60, "хв": 60,
    "годин": 3600, "годину": 3600, "години": 3600, "год": 3600,
}


def parse_relative_time(amount: str, unit: str) -> timedelta | None:
    """Parse "30 minutes" → timedelta.  Returns None on unrecognised unit."""
    try:
        n = int(amount)
    except ValueError:
        return None
    multiplier = _RELATIVE_MULTIPLIERS.get(unit.lower().rstrip("аыуіи"))
    if multiplier is None:
        # Retry exact match
        multiplier = _RELATIVE_MULTIPLIERS.get(unit.lower())
    if multiplier is None:
        return None
    return timedelta(seconds=n * multiplier)


def parse_clock_time(clock: str) -> tuple[int, int] | None:
    """Parse "15:00" or "15.00" or "9" → (hour, minute)."""
    clock = clock.replace(".", ":").strip()
    if ":" in clock:
        parts = clock.split(":")
        try:
            h, m = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return None
    else:
        try:
            h, m = int(clock), 0
        except ValueError:
            return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return (h, m)
    return None


def resolve_absolute_time(hour: int, minute: int) -> datetime:
    """Today at H:M (local), or tomorrow if that time has already passed."""
    now = datetime.now(tz=timezone.utc)
    local_now = now.astimezone()
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)


def resolve_tomorrow_time(hour: int, minute: int) -> datetime:
    """Tomorrow at H:M (local)."""
    local_now = datetime.now(tz=timezone.utc).astimezone()
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=1)
    return target.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class RemindMeArgs(BaseModel):
    text: str = Field(description="What to remind about.")
    seconds: int = Field(
        ge=1, le=604800,
        description="Delay in seconds (1 – 604800 = 7 days).",
    )


class RemindMeAtArgs(BaseModel):
    text: str = Field(description="What to remind about.")
    time: str = Field(description="Time in HH:MM format (24 h).")
    tomorrow: bool = Field(
        default=False,
        description="If True, schedule for tomorrow instead of today.",
    )


class DeleteReminderArgs(BaseModel):
    query: str = Field(description="Text fragment or id to match.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class RemindMeTool:
    """Set a persistent time-based reminder (relative delay)."""

    name: str = "remind_me"
    description: str = (
        "Set a persistent reminder that fires after a delay. "
        "Survives Jarvis restart. Use for 'remind me in 30 min to buy milk'. "
        "(RU: напомни через 30 мин купить молоко / "
        "UK: нагадай через 30 хв купити молоко)"
    )
    args_schema = RemindMeArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "remind me in 30 minutes to buy milk"
        VoicePattern(
            regex=(
                r"^remind\s+me\s+(?:in\s+)?(\d+)\s*"
                r"(minutes?|hours?|mins?|hrs?|h)\s+"
                r"(?:to\s+)?(.+)$"
            ),
            priority=140,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith(("h", "hr")) else 60
                ),
                "text": m.group(3).strip(),
            },
        ),
        # EN: "remind me in 30 minutes" (no text — fallback)
        VoicePattern(
            regex=(
                r"^remind\s+me\s+(?:in\s+)?(\d+)\s*"
                r"(minutes?|hours?|mins?|hrs?|h)$"
            ),
            priority=141,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith(("h", "hr")) else 60
                ),
                "text": "Reminder",
            },
        ),
        # RU: "напомни через 30 минут купить молоко"
        VoicePattern(
            regex=(
                r"^напомни\s+(?:мне\s+)?через\s+(\d+)\s*"
                r"(минут[аыу]?|час(?:ов|а)?|мин|ч)\s+(.+)$"
            ),
            priority=140,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith("час") or m.group(2) == "ч" else 60
                ),
                "text": m.group(3).strip(),
            },
        ),
        # RU: "напомни через 30 минут" (no text)
        VoicePattern(
            regex=(
                r"^напомни\s+(?:мне\s+)?через\s+(\d+)\s*"
                r"(минут[аыу]?|час(?:ов|а)?|мин|ч)$"
            ),
            priority=141,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith("час") or m.group(2) == "ч" else 60
                ),
                "text": "Напоминание",
            },
        ),
        # UK: "нагадай через 30 хвилин купити молоко"
        VoicePattern(
            regex=(
                r"^нагадай\s+(?:мені\s+)?через\s+(\d+)\s*"
                r"(хвилин[уи]?|годин[уи]?|хв|год)\s+(.+)$"
            ),
            priority=140,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith("год") else 60
                ),
                "text": m.group(3).strip(),
            },
        ),
        # UK: "нагадай через 30 хвилин" (no text)
        VoicePattern(
            regex=(
                r"^нагадай\s+(?:мені\s+)?через\s+(\d+)\s*"
                r"(хвилин[уи]?|годин[уи]?|хв|год)$"
            ),
            priority=141,
            args=lambda m: {
                "seconds": int(m.group(1)) * (
                    3600 if m.group(2).startswith("год") else 60
                ),
                "text": "Нагадування",
            },
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: RemindMeArgs) -> ToolResult:
        store = self._get_store()
        trigger_at = datetime.now(tz=timezone.utc) + timedelta(seconds=args.seconds)
        reminder = store.add(text=args.text, trigger_at=trigger_at)

        # Human-readable delay
        if args.seconds >= 3600:
            h = args.seconds // 3600
            m = (args.seconds % 3600) // 60
            delay_str = f"{h}h {m}m" if m else f"{h}h"
        else:
            delay_str = f"{args.seconds // 60}m"

        return ToolResult(
            success=True,
            output=(
                f"Reminder set for {delay_str} from now, sir: "
                f"'{args.text}' (id: {reminder.id})."
            ),
        )


class RemindMeAtTool:
    """Set a persistent time-based reminder at a specific clock time."""

    name: str = "remind_me_at"
    description: str = (
        "Set a persistent reminder at a specific time (HH:MM). "
        "If the time has passed today, schedules for tomorrow. "
        "(RU: напомни в 15:00 / UK: нагадай о 15:00)"
    )
    args_schema = RemindMeAtArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "remind me at 15:00 to call mom"
        VoicePattern(
            regex=(
                r"^remind\s+me\s+(?:at\s+)?(\d{1,2}[:.]\d{2})\s+"
                r"(?:to\s+)?(.+)$"
            ),
            priority=139,
            args=lambda m: {
                "time": m.group(1).replace(".", ":"),
                "text": m.group(2).strip(),
                "tomorrow": False,
            },
        ),
        # EN: "remind me tomorrow at 9:00 to check email"
        VoicePattern(
            regex=(
                r"^remind\s+me\s+tomorrow\s+(?:at\s+)?(\d{1,2}(?:[:.]\d{2})?)\s*"
                r"(?:to\s+)?(.+)?$"
            ),
            priority=138,
            args=lambda m: {
                "time": m.group(1).replace(".", ":") if ":" in m.group(1) or "." in m.group(1) else m.group(1) + ":00",
                "text": (m.group(2) or "Reminder").strip(),
                "tomorrow": True,
            },
        ),
        # RU: "напомни в 15:00 позвонить маме"
        VoicePattern(
            regex=(
                r"^напомни\s+(?:мне\s+)?(?:в\s+)?(\d{1,2}[:.]\d{2})\s+(.+)$"
            ),
            priority=139,
            args=lambda m: {
                "time": m.group(1).replace(".", ":"),
                "text": m.group(2).strip(),
                "tomorrow": False,
            },
        ),
        # RU: "напомни завтра в 9:00 проверить почту"
        VoicePattern(
            regex=(
                r"^напомни\s+(?:мне\s+)?завтра\s+(?:в\s+)?(\d{1,2}(?:[:.]\d{2})?)\s*(.+)?$"
            ),
            priority=138,
            args=lambda m: {
                "time": m.group(1).replace(".", ":") if ":" in m.group(1) or "." in m.group(1) else m.group(1) + ":00",
                "text": (m.group(2) or "Напоминание").strip(),
                "tomorrow": True,
            },
        ),
        # UK: "нагадай о 15:00 зателефонувати мамі"
        VoicePattern(
            regex=(
                r"^нагадай\s+(?:мені\s+)?(?:о\s+)?(\d{1,2}[:.]\d{2})\s+(.+)$"
            ),
            priority=139,
            args=lambda m: {
                "time": m.group(1).replace(".", ":"),
                "text": m.group(2).strip(),
                "tomorrow": False,
            },
        ),
        # UK: "нагадай завтра о 9:00 перевірити пошту"
        VoicePattern(
            regex=(
                r"^нагадай\s+(?:мені\s+)?завтра\s+(?:о\s+)?(\d{1,2}(?:[:.]\d{2})?)\s*(.+)?$"
            ),
            priority=138,
            args=lambda m: {
                "time": m.group(1).replace(".", ":") if ":" in m.group(1) or "." in m.group(1) else m.group(1) + ":00",
                "text": (m.group(2) or "Нагадування").strip(),
                "tomorrow": True,
            },
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: RemindMeAtArgs) -> ToolResult:
        parsed = parse_clock_time(args.time)
        if parsed is None:
            return ToolResult(
                success=False,
                error=f"Cannot parse time '{args.time}'. Use HH:MM format.",
            )
        hour, minute = parsed
        store = self._get_store()

        if args.tomorrow:
            trigger_at = resolve_tomorrow_time(hour, minute)
        else:
            trigger_at = resolve_absolute_time(hour, minute)

        reminder = store.add(text=args.text, trigger_at=trigger_at)
        local_trigger = trigger_at.astimezone()
        time_str = local_trigger.strftime("%H:%M %d %b")

        return ToolResult(
            success=True,
            output=(
                f"Reminder set for {time_str}, sir: "
                f"'{args.text}' (id: {reminder.id})."
            ),
        )


class ListRemindersTool:
    """List all pending scheduled reminders."""

    name: str = "list_reminders"
    description: str = (
        "Lists all pending time-based reminders. "
        "(RU: покажи напоминания, список напоминаний / "
        "UK: покажи нагадування, список нагадувань)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:show|list)\s+(?:my\s+)?reminders?$",
            priority=150,
        ),
        # RU
        VoicePattern(
            regex=r"^(?:покажи|список)\s+(?:мои\s+)?(?:напоминани[яей]|напоминалки?)$",
            priority=150,
        ),
        # UK
        VoicePattern(
            regex=r"^(?:покажи|список)\s+(?:мої\s+)?(?:нагадуванн[яь]|нагадувань)$",
            priority=150,
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: EmptyArgs) -> ToolResult:
        store = self._get_store()
        pending = store.list_pending()
        if not pending:
            return ToolResult(success=True, output="No pending reminders, sir.")
        lines = [f"{len(pending)} pending reminder(s):"]
        for r in pending:
            local_t = r.trigger_at.astimezone()
            lines.append(
                f"  • [{r.id}] {local_t.strftime('%H:%M %d %b')} — {r.text}"
            )
        return ToolResult(success=True, output="\n".join(lines))


class DeleteReminderTool:
    """Cancel (delete) a scheduled reminder by id or text fragment."""

    name: str = "delete_reminder"
    description: str = (
        "Delete/cancel a scheduled reminder by id or text fragment. "
        "(RU: удали напоминание / UK: видали нагадування)"
    )
    args_schema = DeleteReminderArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:delete|cancel|remove)\s+reminder\s+(.+)$",
            priority=150,
            args=lambda m: {"query": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:удали|отмени)\s+(?:напоминание|напоминалку)\s+(.+)$",
            priority=150,
            args=lambda m: {"query": m.group(1).strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:видали|скасуй)\s+(?:нагадування|нагадувалку)\s+(.+)$",
            priority=150,
            args=lambda m: {"query": m.group(1).strip()},
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: DeleteReminderArgs) -> ToolResult:
        store = self._get_store()
        query = args.query.strip()

        # Try direct id match first
        if store.delete(query):
            return ToolResult(
                success=True,
                output=f"Reminder '{query}' deleted, sir.",
            )

        # Fall back to text search
        matches = store.find_by_text(query)
        if not matches:
            return ToolResult(
                success=False,
                error=f"Reminder not found: no match for '{query}'.",
            )

        # Delete the first match
        deleted = matches[0]
        store.delete(deleted.id)
        return ToolResult(
            success=True,
            output=(
                f"Deleted reminder '{deleted.text}' "
                f"(id: {deleted.id}), sir."
            ),
        )
