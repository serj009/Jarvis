"""Repeat the last thing JARVIS said.

The user didn't hear the response — "repeat that", "повтори",
"що ти сказав?" — and wants it spoken again verbatim.

This is a pure READ tool: no side effects, no confirmation.
Voice patterns are high-priority (100) so the short, unambiguous
phrases bypass the LLM entirely and execute as snap commands.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from jarvis.core.last_spoken import get_last_spoken
from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel


class RepeatLastTool:
    """Return the most recently spoken text so the pipeline speaks it again."""

    name: str = "repeat_last"
    description: str = (
        "Repeat the last thing JARVIS said. Use when the user asks "
        "to hear the previous answer again. "
        "(RU: повтори, что ты сказал / UK: повтори, що ти сказав)"
    )
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ

    # ── voice patterns ────────────────────────────────────────────────
    # Priority 100: highest priority, exact-match, no parameters.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(regex=r"^repeat(?:\s+that)?$", priority=100),
        VoicePattern(regex=r"^what\s+did\s+you\s+say\??$", priority=100),
        VoicePattern(regex=r"^say\s+(?:that\s+)?again$", priority=100),
        VoicePattern(regex=r"^come\s+again\??$", priority=100),
        # RU
        VoicePattern(regex=r"^повтори$", priority=100),
        VoicePattern(regex=r"^что\s+(?:ты\s+)?сказал\??$", priority=100),
        VoicePattern(regex=r"^скажи\s+ещё\s+раз$", priority=100),
        # UK
        VoicePattern(regex=r"^повтори$", priority=100),  # same as RU
        VoicePattern(regex=r"^що\s+(?:ти\s+)?сказав\??$", priority=100),
        VoicePattern(regex=r"^скажи\s+ще\s+раз$", priority=100),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        text = get_last_spoken()
        if text:
            return ToolResult(success=True, output=text)

        # Nothing spoken yet — tell the user.
        from jarvis.core.phrases import reply_language

        lang = reply_language()
        if lang == "ru":
            msg = "Я ещё ничего не говорил, сэр."
        elif lang == "uk":
            msg = "Я ще нічого не казав, сер."
        else:
            msg = "I haven't said anything yet, sir."
        return ToolResult(success=True, output=msg)
