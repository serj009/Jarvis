"""Monitor the screen for visual changes.

Wraps ``ScreenChangeDetector`` as a Jarvis tool with four actions:
- **start** — begin periodic screen monitoring.
- **stop**  — stop monitoring.
- **status** — report whether the screen changed since last check.
- **wait**  — block (async) until the screen changes, with a timeout.

Voice patterns cover English, Russian, and Ukrainian.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL: float = 2.0   # seconds between polls
_DEFAULT_TIMEOUT: float = 120.0  # max seconds for "wait"


class WatchScreenArgs(BaseModel):
    action: str = Field(
        default="start",
        description=(
            "One of: start, stop, status, wait.  "
            "'start' begins monitoring; 'stop' ends it; "
            "'status' reports if the screen changed since last check; "
            "'wait' blocks until a change is detected (or timeout)."
        ),
    )
    threshold: float = Field(
        default=0.02,
        description="Fraction of pixels that must change (0.0–1.0).",
    )
    interval: float = Field(
        default=_DEFAULT_INTERVAL,
        description="Seconds between comparison frames (start/wait).",
    )
    timeout: float = Field(
        default=_DEFAULT_TIMEOUT,
        description="Max seconds to wait for a change (wait action).",
    )


def _args_start(m) -> dict:  # noqa: ANN001, ARG001
    return {"action": "start"}


def _args_stop(m) -> dict:  # noqa: ANN001, ARG001
    return {"action": "stop"}


def _args_wait(m) -> dict:  # noqa: ANN001, ARG001
    return {"action": "wait"}


class WatchScreenTool:
    """Monitor the screen for significant visual changes."""

    name: str = "watch_screen"
    description: str = (
        "Monitors the screen for visual changes. Actions: "
        "'start' begins background monitoring, 'stop' ends it, "
        "'status' checks if the screen changed since last poll, "
        "'wait' blocks until a change is detected (with timeout). "
        "(RU: следи за экраном, скажи когда экран изменится / "
        "UK: стеж за екраном, скажи коли екран зміниться)"
    )
    args_schema: type[BaseModel] = WatchScreenArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — "watch the screen", "watch screen"
        VoicePattern(
            regex=r"^watch\s+(?:the\s+)?screen$",
            priority=370,
            args=_args_start,
        ),
        # EN — "tell me when the screen changes", "notify me when screen changes"
        VoicePattern(
            regex=(
                r"^(?:tell|notify)\s+me\s+when\s+(?:the\s+)?"
                r"screen\s+changes$"
            ),
            priority=370,
            args=_args_wait,
        ),
        # EN — "stop watching the screen", "stop watching screen"
        VoicePattern(
            regex=r"^stop\s+watching\s+(?:the\s+)?screen$",
            priority=370,
            args=_args_stop,
        ),
        # RU — "следи за экраном"
        VoicePattern(
            regex=r"^следи\s+за\s+экраном$",
            priority=370,
            args=_args_start,
        ),
        # RU — "скажи когда экран изменится/поменяется"
        VoicePattern(
            regex=(
                r"^скажи\s+когда\s+экран\s+"
                r"(?:изменится|поменяется)$"
            ),
            priority=370,
            args=_args_wait,
        ),
        # RU — "перестань/хватит следить за экраном"
        VoicePattern(
            regex=r"^(?:перестань|хватит)\s+следить\s+за\s+экраном$",
            priority=370,
            args=_args_stop,
        ),
        # UK — "стеж за екраном"
        VoicePattern(
            regex=r"^стеж\s+за\s+екраном$",
            priority=370,
            args=_args_start,
        ),
        # UK — "скажи коли екран зміниться/поміняється"
        VoicePattern(
            regex=(
                r"^скажи\s+коли\s+екран\s+"
                r"(?:зміниться|поміняється)$"
            ),
            priority=370,
            args=_args_wait,
        ),
        # UK — "перестань/досить стежити за екраном"
        VoicePattern(
            regex=r"^(?:перестань|досить)\s+стежити\s+за\s+екраном$",
            priority=370,
            args=_args_stop,
        ),
    )

    def __init__(self) -> None:
        # Lazily created on first "start" so PIL is not imported at
        # registration time.
        self._detector: object | None = None
        self._monitoring: bool = False
        self._monitor_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # -- execute ------------------------------------------------------

    async def execute(self, args: WatchScreenArgs) -> ToolResult:
        action = args.action.strip().lower()
        if action == "start":
            return await self._do_start(args)
        if action == "stop":
            return self._do_stop()
        if action == "status":
            return await self._do_status()
        if action == "wait":
            return await self._do_wait(args)
        return ToolResult(
            success=False,
            error=f"Unknown action {action!r}. Use start/stop/status/wait.",
        )

    # -- action implementations ---------------------------------------

    async def _do_start(self, args: WatchScreenArgs) -> ToolResult:
        if self._monitoring:
            return ToolResult(
                success=True,
                output="Already watching the screen, sir.",
            )
        self._detector = self._make_detector(args.threshold)
        self._monitoring = True
        # Take an initial baseline frame (run in thread — PIL grabs are
        # synchronous).
        await asyncio.to_thread(self._detector.diff_ratio)
        log.info("screen monitoring started (threshold=%.3f)", args.threshold)
        return ToolResult(
            success=True,
            output="I'm now watching the screen for changes, sir.",
        )

    def _do_stop(self) -> ToolResult:
        if not self._monitoring:
            return ToolResult(
                success=True,
                output="I wasn't watching the screen, sir.",
            )
        self._monitoring = False
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None
        self._detector = None
        log.info("screen monitoring stopped")
        return ToolResult(
            success=True,
            output="Stopped watching the screen, sir.",
        )

    async def _do_status(self) -> ToolResult:
        if self._detector is None or not self._monitoring:
            return ToolResult(
                success=False,
                error="Screen monitoring is not active, sir. Say 'watch the screen' first.",
            )
        changed = await asyncio.to_thread(self._detector.has_changed)
        ratio = self._detector._last_diff_ratio
        if changed:
            return ToolResult(
                success=True,
                output=(
                    f"The screen has changed, sir — "
                    f"{ratio:.1%} of pixels differ."
                ),
            )
        return ToolResult(
            success=True,
            output=(
                f"No significant change, sir — "
                f"only {ratio:.1%} of pixels differ."
            ),
        )

    async def _do_wait(self, args: WatchScreenArgs) -> ToolResult:
        detector = self._make_detector(args.threshold)
        # Establish baseline.
        await asyncio.to_thread(detector.diff_ratio)

        interval = max(0.5, args.interval)
        timeout = max(1.0, args.timeout)
        elapsed = 0.0

        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            changed = await asyncio.to_thread(detector.has_changed)
            if changed:
                ratio = detector._last_diff_ratio
                log.info(
                    "screen change detected after %.1fs (%.1f%%)",
                    elapsed, ratio * 100,
                )
                return ToolResult(
                    success=True,
                    output=(
                        f"The screen changed, sir — "
                        f"{ratio:.1%} of pixels differ "
                        f"(detected after {elapsed:.0f}s)."
                    ),
                )

        return ToolResult(
            success=True,
            output=(
                f"No significant screen change detected within "
                f"{timeout:.0f} seconds, sir."
            ),
        )

    # -- helpers ------------------------------------------------------

    @staticmethod
    def _make_detector(threshold: float):  # noqa: ANN205
        """Lazy import + construction of ScreenChangeDetector."""
        from jarvis.vision.screen_change import ScreenChangeDetector

        return ScreenChangeDetector(threshold=threshold)
