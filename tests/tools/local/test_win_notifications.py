"""Tests for jarvis.tools.local.win_notifications.

All notification backends (plyer, win10toast, ctypes) are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from jarvis.tools.local.win_notifications import (
    ScheduleNotificationArgs,
    ScheduleNotificationTool,
    ShowNotificationArgs,
    ShowNotificationTool,
)

# --- ShowNotificationTool -------------------------------------------------

async def test_show_notification_success():
    with patch(
        "jarvis.tools.local.win_notifications._show_notification_impl",
        return_value="Notification shown: 'Test', sir.",
    ):
        result = await ShowNotificationTool().execute(
            ShowNotificationArgs(title="Test", message="Hello world"),
        )
    assert result.success
    assert "Test" in (result.output or "")


async def test_show_notification_not_implemented():
    with patch(
        "jarvis.tools.local.win_notifications._show_notification_impl",
        side_effect=NotImplementedError("plyer required"),
    ):
        result = await ShowNotificationTool().execute(
            ShowNotificationArgs(title="Test", message="Hello"),
        )
    assert not result.success
    assert "plyer" in (result.error or "").lower()


async def test_show_notification_generic_error():
    with patch(
        "jarvis.tools.local.win_notifications._show_notification_impl",
        side_effect=RuntimeError("unknown error"),
    ):
        result = await ShowNotificationTool().execute(
            ShowNotificationArgs(title="Test", message="Hello"),
        )
    assert not result.success


def test_show_notification_timeout_bounds():
    ShowNotificationArgs(title="T", message="M", timeout=1)  # OK
    ShowNotificationArgs(title="T", message="M", timeout=60)  # OK
    with pytest.raises(ValidationError):
        ShowNotificationArgs(title="T", message="M", timeout=0)
    with pytest.raises(ValidationError):
        ShowNotificationArgs(title="T", message="M", timeout=61)


# --- ScheduleNotificationTool --------------------------------------------

async def test_schedule_notification_success():
    result = await ScheduleNotificationTool().execute(
        ScheduleNotificationArgs(
            title="Reminder",
            message="Time to stretch!",
            delay_seconds=300,
        ),
    )
    assert result.success
    assert "5 minute" in (result.output or "")


async def test_schedule_notification_seconds():
    result = await ScheduleNotificationTool().execute(
        ScheduleNotificationArgs(
            title="Quick",
            message="Now!",
            delay_seconds=30,
        ),
    )
    assert result.success
    assert "30 second" in (result.output or "")


async def test_schedule_notification_hours():
    result = await ScheduleNotificationTool().execute(
        ScheduleNotificationArgs(
            title="Later",
            message="Much later",
            delay_seconds=7200,
        ),
    )
    assert result.success
    assert "2 hour" in (result.output or "")


def test_schedule_notification_delay_bounds():
    ScheduleNotificationArgs(title="T", message="M", delay_seconds=1)  # OK
    ScheduleNotificationArgs(title="T", message="M", delay_seconds=86400)  # OK
    with pytest.raises(ValidationError):
        ScheduleNotificationArgs(title="T", message="M", delay_seconds=0)
    with pytest.raises(ValidationError):
        ScheduleNotificationArgs(title="T", message="M", delay_seconds=86401)


def test_requires_confirmation_false():
    assert ShowNotificationTool().requires_confirmation is False
    assert ScheduleNotificationTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(ShowNotificationTool.voice_patterns) >= 3
    assert len(ScheduleNotificationTool.voice_patterns) >= 3
