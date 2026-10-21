"""Voice controls for the proactive engine — level, status, dismiss.

Three tools let the user steer how assertive Jarvis is:

- ``set_initiative``    — "be more active" / "don't bother me" / "be quiet"
- ``proactive_status``  — "what's your initiative level"
- ``dismiss_proactive`` — "not now" / "don't remind me about this"

All three operate on a shared ``ProactiveEngine`` injected at construction
(wired in app.py). With no engine (tests, headless) they degrade to a
friendly message rather than raising.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

class SetInitiativeArgs(BaseModel):
    direction: str = Field(
        description=(
            "How to change initiative: 'up' (more active), 'down' (quieter), "
            "or 'silent' (stop unprompted suggestions)."
        ),
    )


class EmptyProactiveArgs(BaseModel):
    pass


class DismissProactiveArgs(BaseModel):
    scope: str = Field(
        default="now",
        description="'now' (just this time) or 'forever' (stop this suggestion).",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class SetInitiativeLevelTool:
    """Raise or lower how proactive Jarvis is."""

    name: str = "set_initiative"
    description: str = (
        "Change how proactively Jarvis makes unprompted suggestions. "
        "'Be more active' raises it; 'don't bother me' lowers it; "
        "'be quiet' silences it. "
        "(RU: будь активнее / не мешай / помолчи — "
        "UK: будь активнішим / не заважай / помовч)"
    )
    args_schema = SetInitiativeArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # up
        VoicePattern(regex=r"^(?:be\s+more\s+active|suggest\s+more\s+often)$",
                     priority=170, args=lambda m: {"direction": "up"}),
        VoicePattern(regex=r"^(?:будь\s+активнее|предлагай\s+чаще)$",
                     priority=170, args=lambda m: {"direction": "up"}),
        VoicePattern(regex=r"^(?:будь\s+активнішим|пропонуй\s+частіше)$",
                     priority=170, args=lambda m: {"direction": "up"}),
        # silent
        VoicePattern(regex=r"^(?:be\s+quiet|stay\s+silent|stop\s+suggesting)$",
                     priority=170, args=lambda m: {"direction": "silent"}),
        VoicePattern(regex=r"^(?:замолчи\s+совсем|помолчи\s+совсем|тихий\s+режим)$",
                     priority=170, args=lambda m: {"direction": "silent"}),
        VoicePattern(regex=r"^(?:помовч\s+зовсім|тихий\s+режим)$",
                     priority=170, args=lambda m: {"direction": "silent"}),
        # down
        VoicePattern(regex=r"^(?:don'?t\s+bother\s+me|less\s+active|not\s+so\s+often)$",
                     priority=170, args=lambda m: {"direction": "down"}),
        VoicePattern(regex=r"^(?:не\s+мешай|не\s+так\s+часто|сам\s+решу)$",
                     priority=170, args=lambda m: {"direction": "down"}),
        VoicePattern(regex=r"^(?:не\s+заважай|не\s+так\s+часто)$",
                     priority=170, args=lambda m: {"direction": "down"}),
    )

    def __init__(self, *, engine=None) -> None:
        self._engine = engine

    async def execute(self, args: SetInitiativeArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Proactive engine is not active, sir.")
        from jarvis.memory.proactive_engine import InitiativeLevel

        direction = args.direction.strip().lower()
        if direction == "up":
            lvl = self._engine.raise_level()
            msg = f"I'll be more active, sir. Initiative: {lvl.value}."
        elif direction == "silent":
            self._engine.set_level(InitiativeLevel.SILENT)
            msg = "I'll stay quiet until you ask, sir."
        else:  # down
            lvl = self._engine.lower_level()
            msg = f"Understood, sir. Initiative lowered to {lvl.value}."
        return ToolResult(success=True, output=msg)


class ProactiveStatusTool:
    """Report the current initiative level and session stats."""

    name: str = "proactive_status"
    description: str = (
        "Report how proactive Jarvis currently is and session stats "
        "(suggested / accepted / dismissed). "
        "(RU: какой уровень инициативы — UK: який рівень ініціативи)"
    )
    args_schema = EmptyProactiveArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:what'?s\s+your\s+initiative(?:\s+level)?"
                           r"|initiative\s+level)$",
                     priority=170),
        VoicePattern(regex=r"^(?:какой\s+уровень\s+инициативы|уровень\s+инициативы)$",
                     priority=170),
        VoicePattern(regex=r"^(?:який\s+рівень\s+ініціативи|рівень\s+ініціативи)$",
                     priority=170),
    )

    def __init__(self, *, engine=None) -> None:
        self._engine = engine

    async def execute(self, args: EmptyProactiveArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Proactive engine is not active, sir.")
        st = self._engine.status()
        return ToolResult(
            success=True,
            output=(
                f"Initiative: {st['level']}, sir. This session I made "
                f"{st['suggested']} suggestions — {st['accepted']} accepted, "
                f"{st['dismissed']} dismissed."
            ),
        )


class DismissProactiveTool:
    """Dismiss the current/last proactive suggestion."""

    name: str = "dismiss_proactive"
    description: str = (
        "Dismiss Jarvis's last unprompted suggestion — just this time, or "
        "for good. (RU: не сейчас / не напоминай об этом — "
        "UK: не зараз / не нагадуй про це)"
    )
    args_schema = DismissProactiveArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:not\s+now|later|remind\s+me\s+later)$",
                     priority=168, args=lambda m: {"scope": "now"}),
        VoicePattern(regex=r"^(?:don'?t\s+remind\s+me\s+about\s+(?:this|that)"
                           r"|never\s+mind\s+this)$",
                     priority=168, args=lambda m: {"scope": "forever"}),
        VoicePattern(regex=r"^(?:не\s+сейчас|позже)$",
                     priority=168, args=lambda m: {"scope": "now"}),
        VoicePattern(regex=r"^(?:не\s+напоминай\s+(?:об\s+этом|про\s+это))$",
                     priority=168, args=lambda m: {"scope": "forever"}),
        VoicePattern(regex=r"^(?:не\s+зараз|пізніше)$",
                     priority=168, args=lambda m: {"scope": "now"}),
        VoicePattern(regex=r"^(?:не\s+нагадуй\s+про\s+це)$",
                     priority=168, args=lambda m: {"scope": "forever"}),
    )

    def __init__(self, *, engine=None, last_key_provider=None) -> None:
        self._engine = engine
        # Optional callable returning the key of the most recent suggestion.
        self._last_key_provider = last_key_provider

    async def execute(self, args: DismissProactiveArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Proactive engine is not active, sir.")
        key = "last"
        if self._last_key_provider is not None:
            try:
                key = self._last_key_provider() or "last"
            except Exception:
                key = "last"
        self._engine.record_dismiss(key)
        if args.scope.strip().lower() == "forever":
            msg = "I won't bring that up again, sir."
        else:
            msg = "Alright, not now, sir."
        return ToolResult(success=True, output=msg)
