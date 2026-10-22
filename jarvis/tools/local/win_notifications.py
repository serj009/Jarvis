"""Windows toast notifications — show and schedule notifications.

Uses plyer or win10toast (lazy import with fallback).
Scheduled notifications use asyncio.create_task with asyncio.sleep.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_plyer_notification():
    """Lazy import plyer.notification."""
    try:
        from plyer import notification
        return notification
    except ImportError:
        return None


def _get_win10toast():
    """Lazy import win10toast."""
    try:
        from win10toast import ToastNotifier
        return ToastNotifier()
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _show_notification_impl(title: str, message: str, timeout: int = 10) -> str:
    """Show a Windows toast notification."""
    # Try plyer first
    notification = _get_plyer_notification()
    if notification is not None:
        try:
            notification.notify(
                title=title,
                message=message,
                timeout=timeout,
                app_name="Jarvis",
            )
            return f"Notification shown: '{title}', sir."
        except Exception as exc:
            log.debug("plyer notification failed: %s", exc)

    # Try win10toast
    toaster = _get_win10toast()
    if toaster is not None:
        try:
            toaster.show_toast(
                title,
                message,
                duration=timeout,
                threaded=True,
            )
            return f"Notification shown: '{title}', sir."
        except Exception as exc:
            log.debug("win10toast failed: %s", exc)

    # Fallback: native MessageBox (blocking but always available)
    try:
        from jarvis import platform as platform_ops
        platform_ops.message_box(
            message, title=f"Jarvis: {title}", flags=0x40,  # MB_ICONINFORMATION
        )
        return f"Notification shown: '{title}', sir."
    except Exception:
        pass

    raise NotImplementedError(
        "Toast notifications require plyer or win10toast. "
        "Install: pip install plyer"
    )


# Storage for scheduled notification tasks (to allow cancellation if needed)
_scheduled_tasks: dict[str, asyncio.Task] = {}


async def _delayed_notification(
    title: str, message: str, delay_seconds: int,
) -> str:
    """Wait, then show a notification."""
    await asyncio.sleep(delay_seconds)
    return _show_notification_impl(title, message)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class ShowNotificationArgs(BaseModel):
    title: str = Field(description="Notification title.")
    message: str = Field(description="Notification body text.")
    timeout: int = Field(
        default=10, ge=1, le=60,
        description="How long the notification stays visible (1-60 seconds).",
    )


class ScheduleNotificationArgs(BaseModel):
    title: str = Field(description="Notification title.")
    message: str = Field(description="Notification body text.")
    delay_seconds: int = Field(
        ge=1, le=86400,
        description="Delay in seconds before showing the notification (1-86400).",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ShowNotificationTool:
    """Show a Windows toast notification immediately."""

    name: str = "show_notification"
    description: str = (
        "Show a desktop toast notification with a title and message. "
        "(RU: покажи уведомление / UK: покажи сповіщення)"
    )
    args_schema = ShowNotificationArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:show|send)\s+(?:a\s+)?(?:notification|alert)(?:\s+(.+))?$",
            priority=200,
            args=lambda m: {
                "title": "Jarvis Notification",
                "message": m.group(1) or "Notification from Jarvis",
            },
        ),
        # RU
        VoicePattern(
            regex=r"^покажи\s+(?:уведомление|алерт)(?:\s+(.+))?$",
            priority=200,
            args=lambda m: {
                "title": "Уведомление Jarvis",
                "message": m.group(1) or "Уведомление от Jarvis",
            },
        ),
        # UK
        VoicePattern(
            regex=r"^покажи\s+(?:сповіщення|алерт)(?:\s+(.+))?$",
            priority=200,
            args=lambda m: {
                "title": "Сповіщення Jarvis",
                "message": m.group(1) or "Сповіщення від Jarvis",
            },
        ),
    )

    async def execute(self, args: ShowNotificationArgs) -> ToolResult:
        try:
            msg = await asyncio.to_thread(
                _show_notification_impl, args.title, args.message, args.timeout,
            )
            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("show_notification failed")
            return ToolResult(success=False, error=f"Notification failed: {e}")


class ScheduleNotificationTool:
    """Schedule a notification after a delay."""

    name: str = "schedule_notification"
    description: str = (
        "Schedule a desktop toast notification to appear after a delay "
        "(in seconds). Max 24 hours. "
        "(RU: напомни через N секунд/минут уведомлением / "
        "UK: нагадай через N секунд/хвилин)"
    )
    args_schema = ScheduleNotificationArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "notify me in 5 minutes", "remind me in 30 seconds"
        VoicePattern(
            regex=r"^(?:notify|remind)\s+me\s+in\s+(\d+)\s+(second|minute|hour)s?$",
            priority=200,
            args=lambda m: {
                "title": "Jarvis Reminder",
                "message": "Time's up!",
                "delay_seconds": int(m.group(1)) * {
                    "second": 1, "minute": 60, "hour": 3600,
                }.get(m.group(2), 1),
            },
        ),
        # RU: "напомни через 5 минут уведомлением"
        VoicePattern(
            regex=r"^напомни\s+через\s+(\d+)\s+(секунд|минут|час).*$",
            priority=200,
            args=lambda m: {
                "title": "Напоминание Jarvis",
                "message": "Время вышло!",
                "delay_seconds": int(m.group(1)) * {
                    "секунд": 1, "минут": 60, "час": 3600,
                }.get(m.group(2), 1),
            },
        ),
        # UK: "нагадай через 5 хвилин"
        VoicePattern(
            regex=r"^нагадай\s+через\s+(\d+)\s+(секунд|хвилин|годин).*$",
            priority=200,
            args=lambda m: {
                "title": "Нагадування Jarvis",
                "message": "Час вийшов!",
                "delay_seconds": int(m.group(1)) * {
                    "секунд": 1, "хвилин": 60, "годин": 3600,
                }.get(m.group(2), 1),
            },
        ),
    )

    async def execute(self, args: ScheduleNotificationArgs) -> ToolResult:
        try:
            # Create an async task for the delayed notification
            task = asyncio.create_task(
                _delayed_notification(
                    args.title, args.message, args.delay_seconds,
                )
            )
            task_id = f"notif_{id(task)}"
            _scheduled_tasks[task_id] = task

            # Format the delay nicely
            if args.delay_seconds >= 3600:
                delay_str = f"{args.delay_seconds // 3600} hour(s)"
            elif args.delay_seconds >= 60:
                delay_str = f"{args.delay_seconds // 60} minute(s)"
            else:
                delay_str = f"{args.delay_seconds} second(s)"

            return ToolResult(
                success=True,
                output=f"Notification scheduled in {delay_str}, sir.",
            )
        except Exception as e:
            log.exception("schedule_notification failed")
            return ToolResult(
                success=False,
                error=f"Failed to schedule notification: {e}",
            )
