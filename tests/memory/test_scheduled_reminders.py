"""Tests for jarvis.memory.scheduled_reminders (persistent time-based reminders)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.memory.scheduled_reminders import (
    ReminderScheduler,
    ReminderStore,
    ScheduledReminder,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    """Fresh SQLite-backed ReminderStore in a temp directory."""
    s = ReminderStore(db_path=tmp_path / "test_reminders.db")
    s.open()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# ReminderStore — CRUD
# ---------------------------------------------------------------------------

class TestReminderStoreAdd:
    def test_add_returns_reminder(self, store: ReminderStore) -> None:
        trigger = datetime.now(tz=UTC) + timedelta(minutes=30)
        r = store.add("buy milk", trigger)
        assert isinstance(r, ScheduledReminder)
        assert r.text == "buy milk"
        assert r.fired is False
        assert len(r.id) == 12

    def test_add_persists(self, store: ReminderStore) -> None:
        trigger = datetime.now(tz=UTC) + timedelta(hours=1)
        r = store.add("call mom", trigger)
        all_reminders = store.list_all()
        assert any(x.id == r.id for x in all_reminders)

    def test_add_multiple(self, store: ReminderStore) -> None:
        for i in range(5):
            store.add(f"item {i}", datetime.now(tz=UTC) + timedelta(minutes=i + 1))
        assert len(store.list_all()) == 5


class TestReminderStoreGetPending:
    def test_pending_returns_overdue(self, store: ReminderStore) -> None:
        # Already in the past → should be pending
        past = datetime.now(tz=UTC) - timedelta(minutes=5)
        store.add("overdue task", past)
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].text == "overdue task"

    def test_pending_excludes_future(self, store: ReminderStore) -> None:
        future = datetime.now(tz=UTC) + timedelta(hours=2)
        store.add("future task", future)
        assert store.get_pending() == []

    def test_pending_excludes_fired(self, store: ReminderStore) -> None:
        past = datetime.now(tz=UTC) - timedelta(minutes=1)
        r = store.add("done task", past)
        store.mark_fired(r.id)
        assert store.get_pending() == []


class TestReminderStoreMarkFired:
    def test_mark_fired(self, store: ReminderStore) -> None:
        trigger = datetime.now(tz=UTC) - timedelta(minutes=1)
        r = store.add("fire me", trigger)
        store.mark_fired(r.id)
        all_r = store.list_all()
        fired_one = [x for x in all_r if x.id == r.id][0]
        assert fired_one.fired is True


class TestReminderStoreDelete:
    def test_delete_existing(self, store: ReminderStore) -> None:
        r = store.add("delete me", datetime.now(tz=UTC) + timedelta(hours=1))
        assert store.delete(r.id) is True
        assert len(store.list_all()) == 0

    def test_delete_nonexistent(self, store: ReminderStore) -> None:
        assert store.delete("no_such_id") is False


class TestReminderStoreListPending:
    def test_list_pending_sorted(self, store: ReminderStore) -> None:
        now = datetime.now(tz=UTC)
        store.add("later", now + timedelta(hours=2))
        store.add("sooner", now + timedelta(hours=1))
        pending = store.list_pending()
        assert len(pending) == 2
        assert pending[0].text == "sooner"
        assert pending[1].text == "later"

    def test_list_pending_excludes_fired(self, store: ReminderStore) -> None:
        now = datetime.now(tz=UTC)
        r = store.add("done", now + timedelta(hours=1))
        store.mark_fired(r.id)
        assert store.list_pending() == []


class TestReminderStoreFindByText:
    def test_find_by_text(self, store: ReminderStore) -> None:
        store.add("buy milk", datetime.now(tz=UTC) + timedelta(hours=1))
        store.add("call mom", datetime.now(tz=UTC) + timedelta(hours=2))
        matches = store.find_by_text("milk")
        assert len(matches) == 1
        assert matches[0].text == "buy milk"

    def test_find_case_insensitive(self, store: ReminderStore) -> None:
        store.add("Buy Milk", datetime.now(tz=UTC) + timedelta(hours=1))
        matches = store.find_by_text("buy milk")
        assert len(matches) == 1

    def test_find_no_match(self, store: ReminderStore) -> None:
        store.add("buy milk", datetime.now(tz=UTC) + timedelta(hours=1))
        matches = store.find_by_text("bread")
        assert len(matches) == 0


class TestReminderStoreLifecycle:
    def test_reopen_preserves_data(self, tmp_path) -> None:
        db = tmp_path / "persist.db"
        s1 = ReminderStore(db_path=db)
        s1.open()
        trigger = datetime.now(tz=UTC) + timedelta(hours=1)
        r = s1.add("survives restart", trigger)
        s1.close()

        s2 = ReminderStore(db_path=db)
        s2.open()
        all_r = s2.list_all()
        assert any(x.id == r.id and x.text == "survives restart" for x in all_r)
        s2.close()


# ---------------------------------------------------------------------------
# ReminderScheduler
# ---------------------------------------------------------------------------

class TestReminderScheduler:
    @pytest.mark.asyncio()
    async def test_scheduler_fires_overdue(self, store: ReminderStore) -> None:
        past = datetime.now(tz=UTC) - timedelta(minutes=1)
        r = store.add("overdue", past)

        fired_ids: list[str] = []
        scheduler = ReminderScheduler(
            store,
            on_fire=lambda rem: fired_ids.append(rem.id),
            check_interval=0.05,
        )

        task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.15)
        scheduler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert r.id in fired_ids
        # Should be marked as fired
        assert store.get_pending() == []

    @pytest.mark.asyncio()
    async def test_scheduler_does_not_fire_future(self, store: ReminderStore) -> None:
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        store.add("future", future)

        fired: list[ScheduledReminder] = []
        scheduler = ReminderScheduler(
            store,
            on_fire=lambda rem: fired.append(rem),
            check_interval=0.05,
        )

        task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.15)
        scheduler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(fired) == 0

    @pytest.mark.asyncio()
    async def test_scheduler_stop(self, store: ReminderStore) -> None:
        scheduler = ReminderScheduler(
            store,
            on_fire=lambda rem: None,
            check_interval=0.05,
        )
        task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        assert scheduler.running is True
        scheduler.stop()
        await asyncio.sleep(0.15)
        assert scheduler.running is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
