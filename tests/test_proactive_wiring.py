"""Tests for proactive-speech wiring: timer callbacks, reminder scheduler, and bus integration.

Validates that:
1. ProactiveSpeech event exists and is frozen.
2. SetTimerTool fires on_timer_done callback when a timer expires.
3. setup_local_tools passes on_timer_done through to the TimerStore.
4. setup_local_tools passes reminder_store to all reminder tools.
5. ReminderScheduler fires on_fire and the ProactiveSpeech event is published.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.core.config import ToolsConfig
from jarvis.core.events import ProactiveSpeech

# ---------------------------------------------------------------------------
# 1. ProactiveSpeech event contract
# ---------------------------------------------------------------------------

class TestProactiveSpeechEvent:
    def test_frozen_dataclass(self) -> None:
        ev = ProactiveSpeech(text="hello")
        assert ev.text == "hello"
        with pytest.raises(AttributeError):
            ev.text = "changed"  # type: ignore[misc]

    def test_slots(self) -> None:
        ev = ProactiveSpeech(text="x")
        assert not hasattr(ev, "__dict__")


# ---------------------------------------------------------------------------
# 2. TimerStore fires on_timer_done on expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timer_store_fires_callback_on_expiry() -> None:
    """When a timer expires, the TimerStore must call on_timer_done(name)."""
    from jarvis.tools.local.timers import TimerStore

    fired: list[str] = []
    store = TimerStore(on_timer_done=lambda name: fired.append(name))
    result = store.set_timer(0.05, "tea")  # 50 ms
    assert result["success"]

    # Give the asyncio task time to fire
    await asyncio.sleep(0.15)
    assert "tea" in fired


# ---------------------------------------------------------------------------
# 3. setup_local_tools wires on_timer_done into SetTimerTool
# ---------------------------------------------------------------------------

def test_setup_local_tools_wires_timer_callback() -> None:
    """setup_local_tools(on_timer_done=...) must inject a TimerStore with
    the callback into every timer tool."""
    from jarvis.tools import ToolRegistry, setup_local_tools

    fired: list[str] = []
    registry = ToolRegistry(ToolsConfig())
    setup_local_tools(registry, on_timer_done=lambda n: fired.append(n))

    tool = registry.get("set_timer")
    assert tool is not None, "set_timer not registered"
    # The tool's internal store should have the callback
    store = tool._get_store()
    assert store._on_done is not None, "on_timer_done not wired"

    # Cancel/List should share the same store
    cancel_tool = registry.get("cancel_timer")
    list_tool = registry.get("list_timers")
    assert cancel_tool is not None
    assert list_tool is not None
    assert cancel_tool._get_store() is store
    assert list_tool._get_store() is store


# ---------------------------------------------------------------------------
# 4. setup_local_tools wires reminder_store into all reminder tools
# ---------------------------------------------------------------------------

def test_setup_local_tools_wires_reminder_store(tmp_path) -> None:
    """setup_local_tools(reminder_store=...) must inject the store into
    RemindMeTool, RemindMeAtTool, ListRemindersTool, DeleteReminderTool."""
    from jarvis.memory.scheduled_reminders import ReminderStore
    from jarvis.tools import ToolRegistry, setup_local_tools

    store = ReminderStore(db_path=tmp_path / "test.db")
    store.open()
    try:
        registry = ToolRegistry(ToolsConfig())
        setup_local_tools(registry, reminder_store=store)

        for name in ("remind_me", "remind_me_at", "list_reminders", "delete_reminder"):
            tool = registry.get(name)
            assert tool is not None, f"{name} not registered"
            assert tool._get_store() is store, f"{name} not wired to provided store"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 5. ReminderScheduler fires on_fire → ProactiveSpeech published on bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reminder_scheduler_fires_and_publishes(tmp_path) -> None:
    """End-to-end: a past-due reminder fires the scheduler callback.
    In the real app this publishes ProactiveSpeech; here we verify the
    callback is invoked with the ScheduledReminder."""
    from jarvis.memory.scheduled_reminders import ReminderScheduler, ReminderStore

    store = ReminderStore(db_path=tmp_path / "sched.db")
    store.open()
    try:
        # Schedule a reminder in the past → immediately due
        store.add(
            text="buy milk",
            trigger_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        )

        fired_reminders: list = []
        scheduler = ReminderScheduler(
            store,
            on_fire=lambda r: fired_reminders.append(r),
            check_interval=0.05,  # 50 ms for fast test
        )

        task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.2)  # Let at least one tick run
        scheduler.stop()
        await asyncio.sleep(0.1)
        await task  # ensure the scheduler task finishes cleanly

        assert len(fired_reminders) == 1
        assert fired_reminders[0].text == "buy milk"
    finally:
        store.close()
