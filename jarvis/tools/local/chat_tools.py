"""Voice tools for the text chat panel.

Open / close the chat panel via voice commands like "open chat",
"открой чат", "відкрий чат".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


class OpenChatTool:
    name: str = "open_chat"
    description: str = (
        "Opens the text chat panel. Call when the user says "
        "'open chat', 'show chat', 'открой чат', or 'відкрий чат'."
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    # 260: same tier as other panel-open tools (clipboard, logs, etc.)
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:open|show|bring\s+up)\s+(?:the\s+)?(?:text\s+)?chat(?:\s+panel)?$",
            priority=260,
        ),
        # RU: "открой чат", "покажи чат"
        VoicePattern(
            regex=r"^(?:открой|покажи)\s+(?:текстовый\s+)?чат$",
            priority=260,
        ),
        # UK: "відкрий чат", "покажи чат"
        VoicePattern(
            regex=r"^(?:відкрий|покажи)\s+(?:текстовий\s+)?чат$",
            priority=260,
        ),
    )

    def __init__(self, *, on_open: Callable[[], None]) -> None:
        self._on_open = on_open

    async def execute(self, args: EmptyArgs) -> ToolResult:  # noqa: ARG002
        self._on_open()
        return ToolResult(
            success=True,
            output="Chat panel is open, sir.",
        )


class CloseChatTool:
    name: str = "close_chat"
    description: str = (
        "Closes the text chat panel. Call for 'close chat', "
        "'закрой чат', or 'закрий чат'."
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:close|hide)\s+(?:the\s+)?(?:text\s+)?chat(?:\s+panel)?$",
            priority=280,
        ),
        # RU: "закрой чат"
        VoicePattern(
            regex=r"^закрой\s+(?:текстовый\s+)?чат$",
            priority=280,
        ),
        # UK: "закрий чат"
        VoicePattern(
            regex=r"^закрий\s+(?:текстовий\s+)?чат$",
            priority=280,
        ),
    )

    def __init__(self, *, on_close: Callable[[], None]) -> None:
        self._on_close = on_close

    async def execute(self, args: EmptyArgs) -> ToolResult:  # noqa: ARG002
        self._on_close()
        return ToolResult(success=True, output="Chat panel closed.")
