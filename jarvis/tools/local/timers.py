"""Timers and alarms — set, cancel, list.

Asyncio-based timers with JSON persistence. When a timer fires,
it invokes an optional callback (designed for TTS notification).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal, Optional

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)

# Default persistence path (inside Jarvis data dir).
def _default_timers_path() -> Path:
    # Single data-dir resolver (jarvis.paths); layout unchanged. Call-time.
    from jarvis.paths import data_path

    return data_path("timers.json")


class _TimerEntry:
    """Internal representation of a running timer."""

    __slots__ = ("name", "duration_sec", "created_at", "task", "cancelled")

    def __init__(self, name: str, duration_sec: float, created_at: float) -> None:
        self.name = name
        self.duration_sec = duration_sec
        self.created_at = created_at
        self.task: asyncio.Task | None = None
        self.cancelled: bool = False

    @property
    def remaining(self) -> float:
        elapsed = time.time() - self.created_at
        return max(0.0, self.duration_sec - elapsed)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_sec": self.duration_sec,
            "created_at": self.created_at,
        }


class TimerStore:
    """Manages timer lifecycle with optional persistence."""

    def __init__(
        self,
        *,
        on_timer_done: Callable[[str], None] | None = None,
        persist_path: Path | None = None,
    ) -> None:
        self._timers: dict[str, _TimerEntry] = {}
        self._on_done = on_timer_done
        self._persist_path = persist_path if persist_path is not None else _default_timers_path()
        self._counter = 0

    # -- persistence ----------------------------------------------------

    def _save(self) -> None:
        try:
            store = EncryptedJsonStore(self._persist_path)
            active = {
                n: t.to_dict() for n, t in self._timers.items()
                if not t.cancelled and t.remaining > 0
            }
            store.save(active)
        except Exception:
            log.debug("Could not save timers", exc_info=True)

    # -- public API -----------------------------------------------------

    def set_timer(self, duration_sec: float, name: str | None = None) -> dict:
        self._counter += 1
        if not name:
            name = f"timer_{self._counter}"

        if name in self._timers and not self._timers[name].cancelled:
            return {"success": False, "error": f"Timer '{name}' already exists."}

        entry = _TimerEntry(name=name, duration_sec=duration_sec, created_at=time.time())

        async def _run():
            await asyncio.sleep(duration_sec)
            if not entry.cancelled:
                log.info("Timer '%s' finished", name)
                if self._on_done:
                    self._on_done(name)
                self._timers.pop(name, None)
                self._save()

        try:
            loop = asyncio.get_running_loop()
            entry.task = loop.create_task(_run())
        except RuntimeError:
            # No running loop — timer will not auto-fire (test scenario).
            pass

        self._timers[name] = entry
        self._save()

        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        dur_text = f"{mins}m {secs}s" if mins else f"{secs}s"
        return {"success": True, "name": name, "duration": dur_text}

    def cancel_timer(self, name: str) -> dict:
        entry = self._timers.get(name)
        if not entry or entry.cancelled:
            return {"success": False, "error": f"Timer '{name}' not found."}
        entry.cancelled = True
        if entry.task and not entry.task.done():
            entry.task.cancel()
        self._timers.pop(name, None)
        self._save()
        return {"success": True, "name": name}

    def list_timers(self) -> list[dict]:
        result = []
        for name, entry in list(self._timers.items()):
            if entry.cancelled:
                continue
            remaining = entry.remaining
            if remaining <= 0:
                continue
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            result.append({
                "name": name,
                "remaining": f"{mins}m {secs}s" if mins else f"{secs}s",
                "remaining_sec": round(remaining, 1),
            })
        return result


# ---------------------------------------------------------------------------
# Singleton store (created lazily by tools)
# ---------------------------------------------------------------------------

_store: TimerStore | None = None


def _get_store() -> TimerStore:
    global _store
    if _store is None:
        _store = TimerStore()
    return _store


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class SetTimerArgs(BaseModel):
    seconds: int = Field(
        ge=1, le=86400,
        description="Timer duration in seconds (1–86400).",
    )
    name: str = Field(
        default="",
        description="Optional timer name/label.",
    )


class CancelTimerArgs(BaseModel):
    name: str = Field(description="Name of the timer to cancel.")


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

class SetTimerTool:
    name: str = "set_timer"
    description: str = (
        "Sets a countdown timer for the given number of seconds. "
        "Optionally give it a name. Notifies when the timer is up. "
        "(RU: таймер, поставь таймер, напомни через / "
        "UK: таймер, постав таймер, нагадай через)"
    )
    args_schema = SetTimerArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "set timer 5 minutes", "timer for 30 seconds"
        VoicePattern(
            regex=r"^(?:set\s+)?timer\s+(?:for\s+)?(\d+)\s*(minutes?|seconds?|mins?|secs?)$",
            priority=150,
            args=lambda m: {
                "seconds": int(m.group(1)) * (60 if m.group(2).startswith("min") else 1),
            },
        ),
        # RU: "таймер на 5 минут", "поставь таймер на 30 секунд"
        VoicePattern(
            regex=r"^(?:поставь\s+)?таймер\s+(?:на\s+)?(\d+)\s*(?:минут[аыу]?|секунд[уыа]?|мин|сек)$",
            priority=150,
            args=lambda m: {
                "seconds": int(m.group(1)) * (60 if "мин" in m.group(0) else 1),
            },
        ),
        # RU: "напомни через 10 минут"
        VoicePattern(
            regex=r"^напомни\s+через\s+(\d+)\s*(?:минут[аыу]?|секунд[уыа]?|мин|сек)$",
            priority=150,
            args=lambda m: {
                "seconds": int(m.group(1)) * (60 if "мин" in m.group(0) else 1),
            },
        ),
        # UK: "таймер на 5 хвилин", "постав таймер на 10 секунд"
        VoicePattern(
            regex=r"^(?:постав\s+)?таймер\s+(?:на\s+)?(\d+)\s*(?:хвилин[уиа]?|секунд[уиа]?|хв|сек)$",
            priority=150,
            args=lambda m: {
                "seconds": int(m.group(1)) * (60 if "хв" in m.group(0) else 1),
            },
        ),
    )

    def __init__(self, *, store: TimerStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> TimerStore:
        return self._store or _get_store()

    async def execute(self, args: SetTimerArgs) -> ToolResult:
        store = self._get_store()
        result = store.set_timer(args.seconds, args.name or None)
        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Timer '{result['name']}' set for {result['duration']}, sir.",
            )
        return ToolResult(success=False, error=result.get("error", "Failed to set timer."))


class CancelTimerTool:
    name: str = "cancel_timer"
    description: str = (
        "Cancels an active timer by name. "
        "(RU: отмени таймер / UK: скасуй таймер)"
    )
    args_schema = CancelTimerArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^cancel\s+timer\s+(.+)$",
            priority=150,
            args=lambda m: {"name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^отмени\s+таймер\s+(.+)$",
            priority=150,
            args=lambda m: {"name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^скасуй\s+таймер\s+(.+)$",
            priority=150,
            args=lambda m: {"name": m.group(1)},
        ),
    )

    def __init__(self, *, store: TimerStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> TimerStore:
        return self._store or _get_store()

    async def execute(self, args: CancelTimerArgs) -> ToolResult:
        store = self._get_store()
        result = store.cancel_timer(args.name)
        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Timer '{args.name}' cancelled, sir.",
            )
        return ToolResult(success=False, error=result.get("error", "Timer not found."))


class ListTimersTool:
    name: str = "list_timers"
    description: str = (
        "Lists all active timers with remaining time. "
        "(RU: какие таймеры, список таймеров / UK: які таймери, список таймерів)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:list|show|active)\s+timers$",
            priority=150,
        ),
        VoicePattern(
            regex=r"^(?:какие|список)\s+таймер(?:ы|ов)$",
            priority=150,
        ),
        VoicePattern(
            regex=r"^(?:які|список)\s+таймер(?:и|ів)$",
            priority=150,
        ),
    )

    def __init__(self, *, store: TimerStore | None = None) -> None:
        self._store = store

    def _get_store(self) -> TimerStore:
        return self._store or _get_store()

    async def execute(self, args: EmptyArgs) -> ToolResult:
        store = self._get_store()
        timers = store.list_timers()
        if not timers:
            return ToolResult(success=True, output="No active timers, sir.")
        lines = [f"{len(timers)} active timer(s):"]
        for t in timers:
            lines.append(f"  • {t['name']} — {t['remaining']} remaining")
        return ToolResult(success=True, output="\n".join(lines))
