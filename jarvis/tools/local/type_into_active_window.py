"""Type a string into the focused window via simulated keystrokes."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

# pyautogui.typewrite has an upper bound on practical input length —
# very long strings tie up the loop and noticeably block other input.
# 2 kB covers every reasonable LLM-mediated typing intent.
_MAX_TYPE_LENGTH = 2000


class TypeIntoActiveWindowArgs(BaseModel):
    text: str = Field(description="Text to type into the focused window.")
    interval_seconds: float = Field(
        default=0.0, ge=0.0, le=0.2,
        description="Delay between keystrokes; 0 is full speed.",
    )


class TypeIntoActiveWindowTool:
    name: str = "type_into_active_window"
    description: str = (
        "Types text via simulated keystrokes into whatever application currently "
        "has keyboard focus. Only use when the user explicitly says 'type <text>' "
        "or 'paste <text> into <app>'. "
        "(RU: напечатай, напиши, введи / UK: надрукуй, напиши, введи)"
    )
    args_schema = TypeIntoActiveWindowArgs

    # Voice patterns — deterministic dispatch for dictation-mode typing.
    #
    # Priority 800: CATCH_ALL territory.  "type X" / "напечатай X" are
    # extremely broad ("<verb> <anything>") and MUST lose to every more-
    # specific pattern.  In particular:
    #   - notes_tools "запиши ..."   (priority 440) — note-taking, not typing
    #   - auto_login  "введи пароль" (priority 200) — password entry
    #   - open_url    "write this down" is notes, not keystrokes
    # The patterns here only fire when nothing else matched first.
    #
    # "напиши" appears in both RU and UK blocks.  The UK copy carries
    # priority 805 so that if both language blocks are scanned the RU one
    # wins (first-match at lower priority number), while still being in
    # the same catch-all band.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^type\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^write\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^enter\s+(.+)$", priority=810,
            args=lambda m: {"text": m.group(1)},
        ),
        # RU
        VoicePattern(
            regex=r"^напечатай\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^напиши\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^введи\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        # UK
        VoicePattern(
            regex=r"^надрукуй\s+(.+)$", priority=800,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^напиши\s+(.+)$", priority=805,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^введи\s+(.+)$", priority=805,
            args=lambda m: {"text": m.group(1)},
        ),
    )

    # The one tool in the built-in set that the user is asked about
    # before it runs. It synthesises arbitrary keystrokes into whatever
    # window happens to hold focus at the moment of execution — which
    # the LLM cannot see and the user may have changed since speaking.
    # A terminal, a browser address bar and a password field are all
    # plausible targets, and none of them can be undone afterwards. It
    # is already disabled by default in ToolsConfig; the gate is what
    # makes turning it on a reasonable thing to do.
    requires_confirmation: bool = True

    def confirmation_summary(self, args: TypeIntoActiveWindowArgs) -> str:
        """What the user reads in the prompt.

        The description is written for the model ("only use when the
        user explicitly says..."), which is not the question being asked
        here. The question is what is about to be typed, and where — so
        this leads with the length and lets the dialog's argument box
        show the text itself."""
        return (
            f"Type {len(args.text)} character(s) as simulated keystrokes "
            "into whichever window has keyboard focus right now."
        )

    async def execute(self, args: TypeIntoActiveWindowArgs) -> ToolResult:
        if len(args.text) > _MAX_TYPE_LENGTH:
            return ToolResult(
                success=False,
                error=(
                    f"text too long ({len(args.text)} chars, "
                    f"max {_MAX_TYPE_LENGTH})"
                ),
            )

        def _do_type() -> None:
            import pyautogui
            pyautogui.typewrite(args.text, interval=args.interval_seconds)

        try:
            await asyncio.to_thread(_do_type)
        except Exception as e:
            return ToolResult(success=False, error=f"typewrite failed: {e}")
        return ToolResult(success=True, output="Typed, sir.")
