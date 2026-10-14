"""Read the current clipboard text and return it as the spoken output."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel

from jarvis.platform import windows as winplat
from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

# Spoken-output cap. Beyond this we summarise the length instead of
# reading every word — a multi-paragraph clipboard would be tedious to
# hear in full and likely useless via TTS anyway.
_SPEAK_CAP_CHARS = 400


class ClipboardTool:
    name: str = "clipboard"
    description: str = (
        "Reads the current clipboard contents. Only use when the user "
        "explicitly asks to read or check the clipboard. "
        "(RU: буфер обмена / UK: буфер обміну)"
    )
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False

    # Voice patterns for deterministic dispatch — skip LLM for clipboard read.
    #
    # Priority 80: high priority, no parameters, exact match.
    # "what's in the clipboard" is unambiguous and very common.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "what's on my clipboard", "read clipboard", "clipboard"
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+(?:on|in)\s+(?:my\s+)?)?clipboard$",
            priority=80,
        ),
        # RU: "что в буфере", "буфер обмена", "прочитай буфер"
        VoicePattern(
            regex=r"^(?:что\s+в\s+буфере|прочитай\s+буфер|буфер\s+обмена)$",
            priority=80,
        ),
        # UK: "що в буфері", "буфер обміну", "прочитай буфер"
        VoicePattern(
            regex=r"^(?:що\s+в\s+буфері|прочитай\s+буфер|буфер\s+обміну)$",
            priority=80,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            text = await asyncio.to_thread(winplat.read_clipboard_text)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except OSError as e:
            return ToolResult(success=False, error=f"clipboard read failed: {e}")
        if not text:
            return ToolResult(success=True, output="The clipboard is empty, sir.")
        if len(text) > _SPEAK_CAP_CHARS:
            preview = text[:_SPEAK_CAP_CHARS].rstrip()
            return ToolResult(
                success=True,
                output=(
                    f"Clipboard has {len(text)} characters. "
                    f"The first part reads: {preview}"
                ),
            )
        return ToolResult(success=True, output=text)
