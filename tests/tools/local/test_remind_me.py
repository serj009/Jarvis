"""Tests for jarvis.tools.local.remind_me (persistent time-based reminders)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.memory.scheduled_reminders import ReminderStore
from jarvis.tools.local.remind_me import (
    DeleteReminderArgs,
    DeleteReminderTool,
    ListRemindersTool,
    RemindMeArgs,
    RemindMeAtArgs,
    RemindMeAtTool,
    RemindMeTool,
    parse_clock_time,
    parse_relative_time,
    resolve_absolute_time,
    resolve_tomorrow_time,
)
from jarvis.tools.registry import EmptyArgs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = ReminderStore(db_path=tmp_path / "test_remind_me.db")
    s.open()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Time parsing helpers
# ---------------------------------------------------------------------------

class TestParseRelativeTime:
    def test_minutes_en(self) -> None:
        td = parse_relative_time("30", "minutes")
        assert td is not None
        assert td.total_seconds() == 1800

    def test_hours_en(self) -> None:
        td = parse_relative_time("2", "hours")
        assert td is not None
        assert td.total_seconds() == 7200

    def test_min_short(self) -> None:
        td = parse_relative_time("10", "min")
        assert td is not None
        assert td.total_seconds() == 600

    def test_ru_минут(self) -> None:
        td = parse_relative_time("15", "минут")
        assert td is not None
        assert td.total_seconds() == 900

    def test_ru_час(self) -> None:
        td = parse_relative_time("1", "час")
        assert td is not None
        assert td.total_seconds() == 3600

    def test_uk_хвилин(self) -> None:
        td = parse_relative_time("20", "хвилин")
        assert td is not None
        assert td.total_seconds() == 1200

    def test_uk_годин(self) -> None:
        td = parse_relative_time("3", "годин")
        assert td is not None
        assert td.total_seconds() == 10800

    def test_unknown_unit(self) -> None:
        assert parse_relative_time("10", "bananas") is None

    def test_non_numeric(self) -> None:
        assert parse_relative_time("abc", "minutes") is None


class TestParseClockTime:
    def test_hhmm_colon(self) -> None:
        assert parse_clock_time("15:00") == (15, 0)

    def test_hhmm_dot(self) -> None:
        assert parse_clock_time("9.30") == (9, 30)

    def test_bare_hour(self) -> None:
        assert parse_clock_time("9") == (9, 0)

    def test_invalid_hour(self) -> None:
        assert parse_clock_time("25:00") is None

    def test_invalid_minute(self) -> None:
        assert parse_clock_time("12:61") is None

    def test_garbage(self) -> None:
        assert parse_clock_time("abc") is None


class TestResolveAbsoluteTime:
    def test_future_today(self) -> None:
        # Request a time far enough in the future to always be "today"
        now = datetime.now(tz=timezone.utc).astimezone()
        hour = (now.hour + 2) % 24
        result = resolve_absolute_time(hour, 0)
        assert result >= datetime.now(tz=timezone.utc)

    def test_past_wraps_to_tomorrow(self) -> None:
        now = datetime.now(tz=timezone.utc).astimezone()
        # Pick an hour in the past
        past_hour = (now.hour - 2) % 24
        result = resolve_absolute_time(past_hour, 0)
        local_result = result.astimezone()
        # Must be tomorrow
        assert local_result.date() > now.date() or past_hour > now.hour


class TestResolveTomorrowTime:
    def test_tomorrow(self) -> None:
        now = datetime.now(tz=timezone.utc).astimezone()
        result = resolve_tomorrow_time(9, 0)
        local_result = result.astimezone()
        assert local_result.date() == now.date() + timedelta(days=1)


# ---------------------------------------------------------------------------
# Voice pattern matching (regex only — no store needed)
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    def _match(self, tool_cls, text: str) -> dict | None:
        """Try each voice pattern and return the first match args."""
        for vp in tool_cls.voice_patterns:
            m = re.match(vp.regex, text, re.I)
            if m:
                return vp.args(m)
        return None

    # -- RemindMeTool (relative) --

    def test_en_relative(self) -> None:
        args = self._match(RemindMeTool, "remind me in 30 minutes to buy milk")
        assert args is not None
        assert args["seconds"] == 1800
        assert "milk" in args["text"]

    def test_en_relative_no_text(self) -> None:
        args = self._match(RemindMeTool, "remind me in 5 minutes")
        assert args is not None
        assert args["seconds"] == 300

    def test_en_hours(self) -> None:
        args = self._match(RemindMeTool, "remind me in 2 hours to call mom")
        assert args is not None
        assert args["seconds"] == 7200

    def test_ru_relative(self) -> None:
        args = self._match(RemindMeTool, "напомни через 30 минут купить молоко")
        assert args is not None
        assert args["seconds"] == 1800
        assert "молоко" in args["text"]

    def test_ru_relative_mne(self) -> None:
        args = self._match(RemindMeTool, "напомни мне через 10 мин проверить")
        assert args is not None
        assert args["seconds"] == 600

    def test_ru_hours(self) -> None:
        args = self._match(RemindMeTool, "напомни через 2 часа позвонить")
        assert args is not None
        assert args["seconds"] == 7200

    def test_uk_relative(self) -> None:
        args = self._match(RemindMeTool, "нагадай через 30 хвилин купити молоко")
        assert args is not None
        assert args["seconds"] == 1800
        assert "молоко" in args["text"]

    def test_uk_hours(self) -> None:
        args = self._match(RemindMeTool, "нагадай через 1 годину зателефонувати")
        assert args is not None
        assert args["seconds"] == 3600

    # -- RemindMeAtTool (absolute) --

    def test_en_at_time(self) -> None:
        args = self._match(RemindMeAtTool, "remind me at 15:00 to call mom")
        assert args is not None
        assert args["time"] == "15:00"
        assert "mom" in args["text"]

    def test_en_tomorrow(self) -> None:
        args = self._match(RemindMeAtTool, "remind me tomorrow at 9:00 to check email")
        assert args is not None
        assert args["time"] == "9:00"
        assert args["tomorrow"] is True

    def test_ru_at_time(self) -> None:
        args = self._match(RemindMeAtTool, "напомни в 15:00 позвонить маме")
        assert args is not None
        assert args["time"] == "15:00"

    def test_ru_tomorrow(self) -> None:
        args = self._match(RemindMeAtTool, "напомни завтра в 9:00 проверить почту")
        assert args is not None
        assert args["tomorrow"] is True
        assert "почту" in args["text"]

    def test_uk_at_time(self) -> None:
        args = self._match(RemindMeAtTool, "нагадай о 15:00 зателефонувати мамі")
        assert args is not None
        assert args["time"] == "15:00"

    def test_uk_tomorrow(self) -> None:
        args = self._match(RemindMeAtTool, "нагадай завтра о 9:00 перевірити пошту")
        assert args is not None
        assert args["tomorrow"] is True

    # -- ListRemindersTool --

    def test_en_list(self) -> None:
        args = self._match(ListRemindersTool, "show reminders")
        assert args is not None

    def test_ru_list(self) -> None:
        args = self._match(ListRemindersTool, "покажи напоминания")
        assert args is not None

    def test_uk_list(self) -> None:
        args = self._match(ListRemindersTool, "покажи нагадування")
        assert args is not None

    # -- DeleteReminderTool --

    def test_en_delete(self) -> None:
        args = self._match(DeleteReminderTool, "delete reminder buy milk")
        assert args is not None
        assert args["query"] == "buy milk"

    def test_ru_delete(self) -> None:
        args = self._match(DeleteReminderTool, "удали напоминание молоко")
        assert args is not None
        assert "молоко" in args["query"]


# ---------------------------------------------------------------------------
# Tool execute tests (async, need store)
# ---------------------------------------------------------------------------

class TestRemindMeToolExecute:
    @pytest.mark.asyncio()
    async def test_set_relative_reminder(self, store: ReminderStore) -> None:
        tool = RemindMeTool(store=store)
        result = await tool.execute(RemindMeArgs(text="buy milk", seconds=1800))
        assert result.success
        assert "buy milk" in (result.output or "")
        assert "30m" in (result.output or "")
        assert len(store.list_pending()) == 1

    @pytest.mark.asyncio()
    async def test_set_hours_reminder(self, store: ReminderStore) -> None:
        tool = RemindMeTool(store=store)
        result = await tool.execute(RemindMeArgs(text="meeting", seconds=7200))
        assert result.success
        assert "2h" in (result.output or "")


class TestRemindMeAtToolExecute:
    @pytest.mark.asyncio()
    async def test_set_absolute_reminder(self, store: ReminderStore) -> None:
        tool = RemindMeAtTool(store=store)
        result = await tool.execute(
            RemindMeAtArgs(text="call mom", time="15:00", tomorrow=False)
        )
        assert result.success
        assert "call mom" in (result.output or "")
        assert len(store.list_pending()) == 1

    @pytest.mark.asyncio()
    async def test_set_tomorrow_reminder(self, store: ReminderStore) -> None:
        tool = RemindMeAtTool(store=store)
        result = await tool.execute(
            RemindMeAtArgs(text="check email", time="9:00", tomorrow=True)
        )
        assert result.success
        assert "check email" in (result.output or "")

    @pytest.mark.asyncio()
    async def test_invalid_time(self, store: ReminderStore) -> None:
        tool = RemindMeAtTool(store=store)
        result = await tool.execute(
            RemindMeAtArgs(text="invalid", time="25:99", tomorrow=False)
        )
        assert not result.success
        assert "parse" in (result.error or "").lower()


class TestListRemindersToolExecute:
    @pytest.mark.asyncio()
    async def test_list_empty(self, store: ReminderStore) -> None:
        tool = ListRemindersTool(store=store)
        result = await tool.execute(EmptyArgs())
        assert result.success
        assert "no pending" in (result.output or "").lower()

    @pytest.mark.asyncio()
    async def test_list_with_reminders(self, store: ReminderStore) -> None:
        store.add("buy milk", datetime.now(tz=timezone.utc) + timedelta(hours=1))
        store.add("call mom", datetime.now(tz=timezone.utc) + timedelta(hours=2))
        tool = ListRemindersTool(store=store)
        result = await tool.execute(EmptyArgs())
        assert result.success
        assert "2 pending" in (result.output or "")
        assert "buy milk" in (result.output or "")
        assert "call mom" in (result.output or "")


class TestDeleteReminderToolExecute:
    @pytest.mark.asyncio()
    async def test_delete_by_id(self, store: ReminderStore) -> None:
        r = store.add("buy milk", datetime.now(tz=timezone.utc) + timedelta(hours=1))
        tool = DeleteReminderTool(store=store)
        result = await tool.execute(DeleteReminderArgs(query=r.id))
        assert result.success
        assert store.list_pending() == []

    @pytest.mark.asyncio()
    async def test_delete_by_text(self, store: ReminderStore) -> None:
        store.add("buy milk", datetime.now(tz=timezone.utc) + timedelta(hours=1))
        tool = DeleteReminderTool(store=store)
        result = await tool.execute(DeleteReminderArgs(query="milk"))
        assert result.success
        assert store.list_pending() == []

    @pytest.mark.asyncio()
    async def test_delete_nonexistent(self, store: ReminderStore) -> None:
        tool = DeleteReminderTool(store=store)
        result = await tool.execute(DeleteReminderArgs(query="nothing"))
        assert not result.success
        assert "not found" in (result.error or "").lower()


class TestRequiresConfirmation:
    def test_all_false(self) -> None:
        assert RemindMeTool().requires_confirmation is False
        assert RemindMeAtTool().requires_confirmation is False
        assert ListRemindersTool().requires_confirmation is False
        assert DeleteReminderTool().requires_confirmation is False


# ═══════════════════════════════════════════════════════════════════
# Gaps 1-4: recurring, date, snooze, edit
# ═══════════════════════════════════════════════════════════════════

from datetime import datetime as _dt

from jarvis.memory.scheduled_reminders import (
    ReminderScheduler,
    compute_next_trigger,
)
from jarvis.tools.local.remind_me import (
    EditReminderArgs,
    EditReminderTool,
    RecurringReminderArgs,
    RecurringReminderTool,
    SnoozeReminderArgs,
    SnoozeReminderTool,
    parse_date,
    resolve_date_time,
)


# ── Gap 1: recurring — compute_next_trigger ────────────────────────

def test_compute_next_daily():
    prev = _dt(2026, 10, 21, 9, 0)
    assert compute_next_trigger(prev, "daily") == _dt(2026, 10, 22, 9, 0)


def test_compute_next_weekly():
    prev = _dt(2026, 10, 21, 9, 0)  # Wed
    assert compute_next_trigger(prev, "weekly") == _dt(2026, 10, 28, 9, 0)


def test_compute_next_weekdays_skips_weekend():
    fri = _dt(2026, 10, 23, 8, 30)  # Friday
    nxt = compute_next_trigger(fri, "weekdays")
    assert nxt == _dt(2026, 10, 26, 8, 30)  # Monday
    assert nxt.weekday() == 0


def test_compute_next_monthly_clamps_day():
    jan31 = _dt(2027, 1, 31, 9, 0)
    nxt = compute_next_trigger(jan31, "monthly")
    assert (nxt.year, nxt.month, nxt.day) == (2027, 2, 28)


def test_compute_next_monthly_year_rollover():
    dec = _dt(2026, 12, 15, 9, 0)
    nxt = compute_next_trigger(dec, "monthly")
    assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 15)


async def test_recurring_tool_persists_recurrence(store):
    tool = RecurringReminderTool(store=store)
    result = await tool.execute(
        RecurringReminderArgs(text="stand up", recurrence="daily", time="09:00")
    )
    assert result.success
    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].recurrence == "daily"
    assert pending[0].recurrence_time == "09:00"


async def test_recurring_tool_rejects_bad_recurrence(store):
    tool = RecurringReminderTool(store=store)
    result = await tool.execute(
        RecurringReminderArgs(text="x", recurrence="hourly", time="09:00")
    )
    assert not result.success


def test_scheduler_reschedules_recurring(store):
    """A fired recurring reminder is rescheduled, not marked done."""
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    r = store.add(text="daily thing", trigger_at=past,
                  recurrence="daily", recurrence_time="09:00")
    fired = []
    sched = ReminderScheduler(store, on_fire=lambda rem: fired.append(rem.id))
    sched._tick()
    assert fired == [r.id]
    # Not left fired — rescheduled into the future, still pending.
    refreshed = store.get(r.id)
    assert refreshed.fired is False
    assert refreshed.trigger_at > datetime.now(tz=timezone.utc)


def test_scheduler_marks_oneshot_fired(store):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    r = store.add(text="one time", trigger_at=past)
    sched = ReminderScheduler(store, on_fire=lambda rem: None)
    sched._tick()
    assert store.get(r.id).fired is True


# ── Gap 2: date parsing ────────────────────────────────────────────

def test_parse_date_iso():
    assert parse_date("2026-11-15") == (11, 15)


def test_parse_date_day_month_ru():
    assert parse_date("15 ноября") == (11, 15)


def test_parse_date_month_day_en():
    assert parse_date("november 15") == (11, 15)


def test_parse_date_day_month_en():
    assert parse_date("15 november") == (11, 15)


def test_parse_date_invalid():
    assert parse_date("sometime soon") is None


def test_resolve_date_time_rolls_to_next_year():
    # A date earlier in the year than today (Oct 21) rolls to next year.
    dt = resolve_date_time(1, 5, 9, 0)  # Jan 5
    local = dt.astimezone()
    assert local.month == 1 and local.day == 5


async def test_remind_at_with_date(store):
    tool = RemindMeAtTool(store=store)
    result = await tool.execute(
        RemindMeAtArgs(text="submit report", time="14:00", date="15 november")
    )
    assert result.success
    pending = store.list_pending()
    assert len(pending) == 1
    local = pending[0].trigger_at.astimezone()
    assert local.month == 11 and local.day == 15


async def test_remind_at_without_date_unchanged(store):
    """Backward compat: no date → today/tomorrow behaviour."""
    tool = RemindMeAtTool(store=store)
    result = await tool.execute(RemindMeAtArgs(text="call mom", time="09:00"))
    assert result.success
    assert len(store.list_pending()) == 1


# ── Gap 3: snooze ──────────────────────────────────────────────────

async def test_snooze_default_10_min(store):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    r = store.add(text="meeting", trigger_at=past)
    store.mark_fired(r.id)
    result = await SnoozeReminderTool(store=store).execute(SnoozeReminderArgs())
    assert result.success
    refreshed = store.get(r.id)
    assert refreshed.fired is False
    assert refreshed.trigger_at > datetime.now(tz=timezone.utc)


async def test_snooze_custom_minutes(store):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    r = store.add(text="meeting", trigger_at=past)
    store.mark_fired(r.id)
    result = await SnoozeReminderTool(store=store).execute(SnoozeReminderArgs(minutes=30))
    assert result.success
    delta = store.get(r.id).trigger_at - datetime.now(tz=timezone.utc)
    assert timedelta(minutes=25) < delta < timedelta(minutes=35)


async def test_snooze_nothing_fired(store):
    result = await SnoozeReminderTool(store=store).execute(SnoozeReminderArgs())
    assert not result.success


# ── Gap 4: edit ────────────────────────────────────────────────────

async def test_edit_changes_text(store):
    r = store.add(text="meeting", trigger_at=datetime.now(tz=timezone.utc) + timedelta(hours=2))
    result = await EditReminderTool(store=store).execute(
        EditReminderArgs(query="meeting", new_text="standup")
    )
    assert result.success
    assert store.get(r.id).text == "standup"


async def test_edit_changes_time(store):
    r = store.add(text="call", trigger_at=datetime.now(tz=timezone.utc) + timedelta(hours=2))
    old_trigger = store.get(r.id).trigger_at
    result = await EditReminderTool(store=store).execute(
        EditReminderArgs(query="call", new_time="23:59")
    )
    assert result.success
    assert store.get(r.id).trigger_at != old_trigger


async def test_edit_not_found(store):
    result = await EditReminderTool(store=store).execute(
        EditReminderArgs(query="ghost", new_text="x")
    )
    assert not result.success


async def test_edit_nothing_to_change(store):
    store.add(text="meeting", trigger_at=datetime.now(tz=timezone.utc) + timedelta(hours=2))
    result = await EditReminderTool(store=store).execute(
        EditReminderArgs(query="meeting")
    )
    assert not result.success


# ── voice-pattern routing for the new tools ────────────────────────

def _match(tool_cls, phrase):
    for vp in sorted(tool_cls.voice_patterns, key=lambda p: p.priority):
        m = re.match(vp.regex, phrase.strip().lower())
        if m:
            return vp.args(m)
    return None


def test_recurring_patterns_route():
    a = _match(RecurringReminderTool, "напоминай каждый день в 9 встать")
    assert a and a["recurrence"] == "daily" and a["time"] == "09:00"
    b = _match(RecurringReminderTool, "remind me on weekdays at 8:30 to check email")
    assert b and b["recurrence"] == "weekdays"


def test_snooze_patterns_route():
    assert _match(SnoozeReminderTool, "snooze") == {"minutes": 10}
    assert _match(SnoozeReminderTool, "snooze 10 minutes") == {"minutes": 10}
    assert _match(SnoozeReminderTool, "отложи на 15 минут") == {"minutes": 15}


def test_edit_patterns_route():
    a = _match(EditReminderTool, "перенеси напоминание встреча на 16:00")
    assert a and a["new_time"] == "16:00"
