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
