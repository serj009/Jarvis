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
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.memory.scheduled_reminders import ReminderStore
from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

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


def _norm_time(clock: str | None) -> str:
    """Normalise an optional spoken time to HH:MM; default 09:00 when absent."""
    if not clock:
        return "09:00"
    c = clock.replace(".", ":").strip()
    if ":" in c:
        h, _, m = c.partition(":")
    else:
        h, m = c, "00"
    try:
        return f"{int(h):02d}:{int(m):02d}"
    except ValueError:
        return "09:00"


def resolve_absolute_time(hour: int, minute: int) -> datetime:
    """Today at H:M (local), or tomorrow if that time has already passed."""
    now = datetime.now(tz=UTC)
    local_now = now.astimezone()
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def resolve_tomorrow_time(hour: int, minute: int) -> datetime:
    """Tomorrow at H:M (local)."""
    local_now = datetime.now(tz=UTC).astimezone()
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=1)
    return target.astimezone(UTC)


# Month name -> number (EN / RU / UK), lowercased. RU/UK use the genitive
# forms spoken in dates ("15 ноября", "15 листопада").
_MONTHS: dict[str, int] = {
    # EN
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # RU (genitive)
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
    "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
    "ноября": 11, "декабря": 12,
    # UK (genitive)
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5,
    "червня": 6, "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10,
    "листопада": 11, "грудня": 12,
}


def parse_date(text: str) -> tuple[int, int] | None:
    """Parse a spoken date into (month, day). None if unparseable.

    Accepts: 'YYYY-MM-DD', '15 ноября', 'november 15', '15 november'.
    """
    t = text.strip().lower()
    # ISO: 2026-11-15
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        month, day = int(m.group(2)), int(m.group(3))
        return (month, day) if 1 <= month <= 12 and 1 <= day <= 31 else None
    # "15 ноября" / "15 november"
    m = re.match(r"^(\d{1,2})\s+([a-zа-яіїєґ]+)$", t)
    if m and m.group(2) in _MONTHS:
        return (_MONTHS[m.group(2)], int(m.group(1)))
    # "november 15" / "ноября 15"
    m = re.match(r"^([a-zа-яіїєґ]+)\s+(\d{1,2})$", t)
    if m and m.group(1) in _MONTHS:
        return (_MONTHS[m.group(1)], int(m.group(2)))
    return None


def resolve_date_time(month: int, day: int, hour: int, minute: int) -> datetime:
    """Resolve a (month, day, H:M) to the next such moment, this year or next.

    If the date/time has already passed this year, roll to next year.
    """
    local_now = datetime.now(tz=UTC).astimezone()
    year = local_now.year
    try:
        target = local_now.replace(
            year=year, month=month, day=day,
            hour=hour, minute=minute, second=0, microsecond=0,
        )
    except ValueError:
        # Impossible date (e.g. Feb 30) — bail to None via caller guard.
        raise
    if target <= local_now:
        target = target.replace(year=year + 1)
    return target.astimezone(UTC)


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
    date: str = Field(
        default="",
        description=(
            "Optional specific date: 'YYYY-MM-DD', '15 november', "
            "'15 ноября'. Empty = today/tomorrow per the tomorrow flag."
        ),
    )


class DeleteReminderArgs(BaseModel):
    query: str = Field(description="Text fragment or id to match.")


class RecurringReminderArgs(BaseModel):
    text: str = Field(description="What to remind about.")
    recurrence: str = Field(
        description="One of: daily, weekdays, weekly, monthly.",
    )
    time: str = Field(description="Time of day in HH:MM (24 h).")


class SnoozeReminderArgs(BaseModel):
    minutes: int = Field(
        default=10, ge=1, le=1440,
        description="Snooze delay in minutes (default 10).",
    )
    time: str = Field(
        default="",
        description="Optional absolute HH:MM to snooze until instead of a delay.",
    )


class EditReminderArgs(BaseModel):
    query: str = Field(description="Text fragment or id of the reminder to edit.")
    new_text: str = Field(default="", description="New reminder text (optional).")
    new_time: str = Field(default="", description="New time HH:MM (optional).")


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
        trigger_at = datetime.now(tz=UTC) + timedelta(seconds=args.seconds)
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
        # ── specific date (priority 137 — beats generic at/tomorrow) ──
        # EN: "remind me on november 15 to submit report",
        #     "remind me on november 15 at 14:00 to submit report"
        VoicePattern(
            regex=(
                r"^remind\s+me\s+on\s+([a-z]+\s+\d{1,2}|\d{1,2}\s+[a-z]+|\d{4}-\d{1,2}-\d{1,2})"
                r"(?:\s+at\s+(\d{1,2}(?:[:.]\d{2})?))?\s+(?:to\s+)?(.+)$"
            ),
            priority=137,
            args=lambda m: {
                "date": m.group(1),
                "time": _norm_time(m.group(2)),
                "text": m.group(3).strip(),
                "tomorrow": False,
            },
        ),
        # RU: "напомни 15 ноября сдать отчёт",
        #     "напомни 15 ноября в 14:00 сдать отчёт"
        VoicePattern(
            regex=(
                r"^напомни\s+(?:мне\s+)?(\d{1,2}\s+[а-яё]+)"
                r"(?:\s+в\s+(\d{1,2}(?:[:.]\d{2})?))?\s+(.+)$"
            ),
            priority=137,
            args=lambda m: {
                "date": m.group(1),
                "time": _norm_time(m.group(2)),
                "text": m.group(3).strip(),
                "tomorrow": False,
            },
        ),
        # UK: "нагадай 15 листопада здати звіт"
        VoicePattern(
            regex=(
                r"^нагадай\s+(?:мені\s+)?(\d{1,2}\s+[а-яіїєґ]+)"
                r"(?:\s+о\s+(\d{1,2}(?:[:.]\d{2})?))?\s+(.+)$"
            ),
            priority=137,
            args=lambda m: {
                "date": m.group(1),
                "time": _norm_time(m.group(2)),
                "text": m.group(3).strip(),
                "tomorrow": False,
            },
        ),
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

        if args.date.strip():
            parsed_date = parse_date(args.date)
            if parsed_date is None:
                return ToolResult(
                    success=False,
                    error=f"Cannot parse date '{args.date}', sir.",
                )
            month, day = parsed_date
            try:
                trigger_at = resolve_date_time(month, day, hour, minute)
            except ValueError:
                return ToolResult(
                    success=False,
                    error=f"'{args.date}' is not a valid date, sir.",
                )
        elif args.tomorrow:
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


class RecurringReminderTool:
    """Set a repeating reminder (daily / weekdays / weekly / monthly)."""

    name: str = "remind_recurring"
    description: str = (
        "Set a repeating reminder at a fixed time of day. Recurrence: "
        "daily, weekdays (Mon–Fri), weekly, or monthly. "
        "(RU: напоминай каждый день / по будням в HH:MM / "
        "UK: нагадуй щодня / по буднях о HH:MM)"
    )
    args_schema = RecurringReminderArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "remind me every day at 9am to stand up"
        VoicePattern(
            regex=(
                r"^remind\s+me\s+(every\s+day|daily|on\s+weekdays|every\s+week"
                r"|weekly|every\s+month|monthly)\s+(?:at\s+)?"
                r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)\s+(?:to\s+)?(.+)$"
            ),
            priority=136,
            args=lambda m: {
                "recurrence": _norm_recurrence(m.group(1)),
                "time": _norm_ampm(m.group(2)),
                "text": m.group(3).strip(),
            },
        ),
        # RU: "напоминай каждый день в 9 встать", "напоминай по будням в 8:30 ..."
        VoicePattern(
            regex=(
                r"^напоминай\s+(каждый\s+день|ежедневно|по\s+будням"
                r"|каждую\s+неделю|еженедельно|каждый\s+месяц|ежемесячно)\s+"
                r"(?:в\s+)?(\d{1,2}(?:[:.]\d{2})?)\s+(.+)$"
            ),
            priority=136,
            args=lambda m: {
                "recurrence": _norm_recurrence(m.group(1)),
                "time": _norm_time(m.group(2)),
                "text": m.group(3).strip(),
            },
        ),
        # UK: "нагадуй щодня о 9 встати", "нагадуй по буднях о 8:30 ..."
        VoicePattern(
            regex=(
                r"^нагадуй\s+(щодня|щоденно|по\s+буднях|щотижня"
                r"|кожного\s+тижня|щомісяця|кожного\s+місяця)\s+"
                r"(?:о\s+)?(\d{1,2}(?:[:.]\d{2})?)\s+(.+)$"
            ),
            priority=136,
            args=lambda m: {
                "recurrence": _norm_recurrence(m.group(1)),
                "time": _norm_time(m.group(2)),
                "text": m.group(3).strip(),
            },
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: RecurringReminderArgs) -> ToolResult:
        recurrence = args.recurrence.strip().lower()
        if recurrence not in {"daily", "weekdays", "weekly", "monthly"}:
            return ToolResult(
                success=False,
                error=(
                    f"Unknown recurrence '{args.recurrence}'. Use daily, "
                    "weekdays, weekly, or monthly, sir."
                ),
            )
        parsed = parse_clock_time(args.time)
        if parsed is None:
            return ToolResult(
                success=False,
                error=f"Cannot parse time '{args.time}'. Use HH:MM format.",
            )
        hour, minute = parsed
        trigger_at = resolve_absolute_time(hour, minute)
        # weekdays: if the first occurrence lands on a weekend, roll forward.
        if recurrence == "weekdays":
            local = trigger_at.astimezone()
            while local.weekday() >= 5:
                local += timedelta(days=1)
            trigger_at = local.astimezone(UTC)

        store = self._get_store()
        reminder = store.add(
            text=args.text,
            trigger_at=trigger_at,
            recurrence=recurrence,
            recurrence_time=f"{hour:02d}:{minute:02d}",
        )
        first = trigger_at.astimezone().strftime("%H:%M %d %b")
        return ToolResult(
            success=True,
            output=(
                f"Recurring reminder set ({recurrence}) at {hour:02d}:{minute:02d}, "
                f"first on {first}, sir: '{args.text}' (id: {reminder.id})."
            ),
        )


class SnoozeReminderTool:
    """Snooze the most recently fired reminder."""

    name: str = "snooze_reminder"
    description: str = (
        "Snooze the reminder that just fired — re-fire it after a delay "
        "(default 10 min) or at a given time. "
        "(RU: отложи / отложи на 10 минут / отложи до 15:00 / "
        "UK: відклади / відклади на 10 хвилин)"
    )
    args_schema = SnoozeReminderArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "snooze until 3pm" / "snooze 10 minutes" / "snooze" / "remind me later"
        VoicePattern(
            regex=r"^snooze\s+until\s+(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)$",
            priority=135,
            args=lambda m: {"time": _norm_ampm(m.group(1)), "minutes": 10},
        ),
        VoicePattern(
            regex=r"^snooze\s+(?:for\s+)?(\d+)\s*(?:minutes?|mins?)$",
            priority=135,
            args=lambda m: {"minutes": int(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:snooze|remind\s+me\s+later)$",
            priority=136,
            args=lambda m: {"minutes": 10},
        ),
        # RU: "отложи до 15:00" / "отложи на 10 минут" / "отложи" / "напомни позже"
        VoicePattern(
            regex=r"^отложи\s+до\s+(\d{1,2}(?:[:.]\d{2})?)$",
            priority=135,
            args=lambda m: {"time": _norm_time(m.group(1)), "minutes": 10},
        ),
        VoicePattern(
            regex=r"^отложи\s+(?:на\s+)?(\d+)\s*(?:минут[аыу]?|мин)$",
            priority=135,
            args=lambda m: {"minutes": int(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:отложи|напомни\s+позже)$",
            priority=136,
            args=lambda m: {"minutes": 10},
        ),
        # UK: "відклади до 15:00" / "відклади на 10 хвилин" / "відклади"
        VoicePattern(
            regex=r"^відклади\s+до\s+(\d{1,2}(?:[:.]\d{2})?)$",
            priority=135,
            args=lambda m: {"time": _norm_time(m.group(1)), "minutes": 10},
        ),
        VoicePattern(
            regex=r"^відклади\s+(?:на\s+)?(\d+)\s*(?:хвилин[иу]?|хв)$",
            priority=135,
            args=lambda m: {"minutes": int(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:відклади|нагадай\s+пізніше)$",
            priority=136,
            args=lambda m: {"minutes": 10},
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: SnoozeReminderArgs) -> ToolResult:
        store = self._get_store()
        last = store.last_fired()
        if last is None:
            return ToolResult(
                success=False,
                error="Nothing to snooze — no reminder has fired, sir.",
            )
        if args.time.strip():
            parsed = parse_clock_time(args.time)
            if parsed is None:
                return ToolResult(
                    success=False,
                    error=f"Cannot parse time '{args.time}', sir.",
                )
            new_trigger = resolve_absolute_time(parsed[0], parsed[1])
            when = new_trigger.astimezone().strftime("%H:%M")
            note = f"until {when}"
        else:
            new_trigger = datetime.now(tz=UTC) + timedelta(minutes=args.minutes)
            note = f"for {args.minutes} min"
        store.update_reminder(last.id, trigger_at=new_trigger, fired=False)
        return ToolResult(
            success=True,
            output=f"Snoozed '{last.text}' {note}, sir.",
        )


class EditReminderTool:
    """Edit an existing reminder's text and/or time."""

    name: str = "edit_reminder"
    description: str = (
        "Change the text or time of an existing reminder without "
        "recreating it. (RU: перенеси/измени напоминание / "
        "UK: перенеси/зміни нагадування)"
    )
    args_schema = EditReminderArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "change my 3pm reminder to 4pm" → handled as reschedule by text match
        VoicePattern(
            regex=r"^(?:change|move|reschedule)\s+(?:my\s+)?reminder\s+(.+?)\s+to\s+(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)$",
            priority=134,
            args=lambda m: {"query": m.group(1).strip(), "new_time": _norm_ampm(m.group(2))},
        ),
        # RU: "перенеси напоминание <текст> на 16:00"
        VoicePattern(
            regex=r"^перенеси\s+напоминание\s+(.+?)\s+на\s+(\d{1,2}(?:[:.]\d{2})?)$",
            priority=134,
            args=lambda m: {"query": m.group(1).strip(), "new_time": _norm_time(m.group(2))},
        ),
        # RU: "измени напоминание <текст> текст на <new>"
        VoicePattern(
            regex=r"^измени\s+напоминание\s+(.+?)\s+текст\s+на\s+(.+)$",
            priority=134,
            args=lambda m: {"query": m.group(1).strip(), "new_text": m.group(2).strip()},
        ),
        # UK: "перенеси нагадування <текст> на 16:00"
        VoicePattern(
            regex=r"^перенеси\s+нагадування\s+(.+?)\s+на\s+(\d{1,2}(?:[:.]\d{2})?)$",
            priority=134,
            args=lambda m: {"query": m.group(1).strip(), "new_time": _norm_time(m.group(2))},
        ),
        # UK: "зміни нагадування <текст> текст на <new>"
        VoicePattern(
            regex=r"^зміни\s+нагадування\s+(.+?)\s+текст\s+на\s+(.+)$",
            priority=134,
            args=lambda m: {"query": m.group(1).strip(), "new_text": m.group(2).strip()},
        ),
    )

    def __init__(self, *, store: ReminderStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> ReminderStore:
        return self._store or _get_store()

    async def execute(self, args: EditReminderArgs) -> ToolResult:
        store = self._get_store()
        query = args.query.strip()
        target = store.get(query)
        if target is None:
            matches = store.find_by_text(query)
            if not matches:
                return ToolResult(
                    success=False,
                    error=f"Reminder not found: no match for '{query}', sir.",
                )
            target = matches[0]

        if not args.new_text.strip() and not args.new_time.strip():
            return ToolResult(
                success=False,
                error="Nothing to change — give new text or a new time, sir.",
            )

        updates: dict = {}
        if args.new_text.strip():
            updates["text"] = args.new_text.strip()
        if args.new_time.strip():
            parsed = parse_clock_time(args.new_time)
            if parsed is None:
                return ToolResult(
                    success=False,
                    error=f"Cannot parse time '{args.new_time}', sir.",
                )
            new_trigger = resolve_absolute_time(parsed[0], parsed[1])
            updates["trigger_at"] = new_trigger
            # Keep recurring reminders' recurrence_time in sync.
            if target.recurrence:
                updates["recurrence_time"] = f"{parsed[0]:02d}:{parsed[1]:02d}"
            # Re-arm a fired reminder when its time moves.
            updates["fired"] = False

        store.update_reminder(target.id, **updates)
        return ToolResult(
            success=True,
            output=f"Updated reminder '{target.text}' (id: {target.id}), sir.",
        )


def _norm_recurrence(word: str) -> str:
    """Map a spoken recurrence phrase (EN/RU/UK) to a canonical rule."""
    w = word.strip().lower()
    mapping = {
        "every day": "daily", "daily": "daily",
        "каждый день": "daily", "ежедневно": "daily",
        "щодня": "daily", "щоденно": "daily",
        "on weekdays": "weekdays", "по будням": "weekdays", "по буднях": "weekdays",
        "every week": "weekly", "weekly": "weekly",
        "каждую неделю": "weekly", "еженедельно": "weekly",
        "щотижня": "weekly", "кожного тижня": "weekly",
        "every month": "monthly", "monthly": "monthly",
        "каждый месяц": "monthly", "ежемесячно": "monthly",
        "щомісяця": "monthly", "кожного місяця": "monthly",
    }
    return mapping.get(w, "daily")


def _norm_ampm(clock: str | None) -> str:
    """Normalise '9am' / '3pm' / '14:00' -> HH:MM; default 09:00 if absent."""
    if not clock:
        return "09:00"
    c = clock.strip().lower().replace(".", ":")
    ampm = None
    if c.endswith("am"):
        ampm = "am"; c = c[:-2].strip()
    elif c.endswith("pm"):
        ampm = "pm"; c = c[:-2].strip()
    if ":" in c:
        h, m = c.split(":", 1)
    else:
        h, m = c, "00"
    try:
        hh = int(h); mm = int(m)
    except ValueError:
        return "09:00"
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    return f"{hh:02d}:{mm:02d}"
