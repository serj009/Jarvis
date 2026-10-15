"""Tests for jarvis.tools.local.timers."""

from __future__ import annotations

import pytest

from jarvis.tools.local.timers import (
    CancelTimerArgs,
    CancelTimerTool,
    ListTimersTool,
    SetTimerArgs,
    SetTimerTool,
    TimerStore,
)
from jarvis.tools.registry import EmptyArgs


# --- TimerStore unit tests -------------------------------------------------

def test_set_timer():
    store = TimerStore()
    result = store.set_timer(60, "my_timer")
    assert result["success"]
    assert result["name"] == "my_timer"
    assert "1m 0s" in result["duration"]


def test_set_timer_auto_name():
    store = TimerStore()
    result = store.set_timer(30)
    assert result["success"]
    assert result["name"].startswith("timer_")


def test_set_duplicate_timer():
    store = TimerStore()
    store.set_timer(60, "dup")
    result = store.set_timer(60, "dup")
    assert not result["success"]
    assert "already exists" in result.get("error", "")


def test_cancel_timer():
    store = TimerStore()
    store.set_timer(600, "cancel_me")
    result = store.cancel_timer("cancel_me")
    assert result["success"]


def test_cancel_nonexistent_timer():
    store = TimerStore()
    result = store.cancel_timer("does_not_exist")
    assert not result["success"]


def test_list_timers_empty():
    store = TimerStore()
    assert store.list_timers() == []


def test_list_timers_with_active():
    store = TimerStore()
    store.set_timer(600, "active_one")
    timers = store.list_timers()
    assert len(timers) == 1
    assert timers[0]["name"] == "active_one"


# --- Tool-level tests (async) ---------------------------------------------

async def test_set_timer_tool():
    store = TimerStore()
    tool = SetTimerTool(store=store)
    result = await tool.execute(SetTimerArgs(seconds=120, name="test"))
    assert result.success
    assert "test" in (result.output or "")
    assert "2m 0s" in (result.output or "")


async def test_cancel_timer_tool():
    store = TimerStore()
    store.set_timer(600, "to_cancel")
    tool = CancelTimerTool(store=store)
    result = await tool.execute(CancelTimerArgs(name="to_cancel"))
    assert result.success


async def test_cancel_timer_tool_not_found():
    store = TimerStore()
    tool = CancelTimerTool(store=store)
    result = await tool.execute(CancelTimerArgs(name="nope"))
    assert not result.success


async def test_list_timers_tool_empty():
    store = TimerStore()
    tool = ListTimersTool(store=store)
    result = await tool.execute(EmptyArgs())
    assert result.success
    assert "no active" in (result.output or "").lower()


async def test_list_timers_tool_with_timers():
    store = TimerStore()
    store.set_timer(300, "countdown")
    tool = ListTimersTool(store=store)
    result = await tool.execute(EmptyArgs())
    assert result.success
    assert "countdown" in (result.output or "")


def test_requires_confirmation_false():
    assert SetTimerTool().requires_confirmation is False
    assert CancelTimerTool().requires_confirmation is False
    assert ListTimersTool().requires_confirmation is False


def test_timer_seconds_bounds():
    with pytest.raises(Exception):
        SetTimerArgs(seconds=0)
    with pytest.raises(Exception):
        SetTimerArgs(seconds=100000)
