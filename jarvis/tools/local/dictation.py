"""Continuous dictation mode — everything you say is typed into the active window.

"Start dictation" activates the mode; every subsequent STT result is
typed into whatever window has keyboard focus via simulated keystrokes
instead of being routed through the LLM.  "Stop dictation" exits.

Special voice commands while dictating::

    "new line" / "новая строка" / "новий рядок"                    →  Enter
    "period" / "точка" / "крапка"                                   →  .
    "comma" / "запятая" / "кома"                                    →  ,
    "question mark" / "вопросительный знак" / "знак питання"        →  ?
    "exclamation mark" / "восклицательный знак" / "знак оклику"     →  !
    "colon" / "двоеточие" / "двокрапка"                             →  :
    "semicolon" / "точка с запятой" / "крапка з комою"              →  ;
    "tab" / "табуляция" / "табуляція"                               →  \\t

Integration with IntentRouter
-----------------------------
``route()`` checks ``is_dictation_active()`` at the very top, before the
open-target planner and the pattern table.  When active:

1. The transcription is normalized and checked against
   ``DICTATION_STOP_PATTERN``.  A match falls through to the normal
   pattern layer so ``DictationTool.execute(stop)`` clears the flag.
2. Any other transcription is processed through
   ``process_dictation_text()`` and typed directly via
   ``type_dictation_text()`` — bypassing the registry (and its
   confirmation gate) because the user already explicitly opted in.

Confirmation bypass rationale
-----------------------------
``type_into_active_window`` carries ``RiskLevel.SYSTEM`` and
``requires_confirmation = True``.  Routing every dictated word through
the registry would pop a confirmation dialog on every utterance, making
the feature unusable.  ``type_dictation_text()`` invokes pyautogui
directly, which is safe here: dictation mode was explicitly activated by
a pattern-matched voice command that DID go through the normal tool
dispatch path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_dictation_active: bool = False


def is_dictation_active() -> bool:
    """Check whether dictation mode is currently on."""
    return _dictation_active


def _set_dictation(active: bool) -> None:
    global _dictation_active  # noqa: PLW0603
    _dictation_active = active


# ---------------------------------------------------------------------------
# Stop pattern — checked by route() to let stop commands through
# ---------------------------------------------------------------------------
# Normalised (lowercased, trailing punctuation stripped, fillers peeled)
# transcription is matched against this.  Keep in sync with the stop
# voice_patterns on DictationTool below.

DICTATION_STOP_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:"
    # EN
    r"(?:stop|end)\s+dictation|stop\s+dictating"
    # RU
    r"|(?:закончи|стоп|останови|прекрати)\s+диктовк[уа]"
    # UK
    r"|(?:заверши|стоп|зупини|припини)\s+диктовк[уа]"
    r")$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Special commands — expanded during dictation
# ---------------------------------------------------------------------------
# Ordered longest-first so "точка с запятой" matches before "точка".

_SPECIAL_COMMANDS: list[tuple[re.Pattern[str], str]] = [
    # --- multi-word (match first) ---
    (re.compile(r"\bточка с запятой\b", re.IGNORECASE), ";"),
    (re.compile(r"\bкрапка з комою\b", re.IGNORECASE), ";"),
    (re.compile(r"\bвопросительный знак\b", re.IGNORECASE), "?"),
    (re.compile(r"\bвосклицательный знак\b", re.IGNORECASE), "!"),
    (re.compile(r"\bзнак питання\b", re.IGNORECASE), "?"),
    (re.compile(r"\bзнак оклику\b", re.IGNORECASE), "!"),
    (re.compile(r"\bnew line\b", re.IGNORECASE), "\n"),
    (re.compile(r"\bновая строка\b", re.IGNORECASE), "\n"),
    (re.compile(r"\bновий рядок\b", re.IGNORECASE), "\n"),
    (re.compile(r"\bquestion mark\b", re.IGNORECASE), "?"),
    (re.compile(r"\bexclamation mark\b", re.IGNORECASE), "!"),
    (re.compile(r"\bexclamation point\b", re.IGNORECASE), "!"),
    # --- single-word ---
    (re.compile(r"\bperiod\b", re.IGNORECASE), "."),
    (re.compile(r"\bточка\b", re.IGNORECASE), "."),
    (re.compile(r"\bкрапка\b", re.IGNORECASE), "."),
    (re.compile(r"\bcomma\b", re.IGNORECASE), ","),
    (re.compile(r"\bзапятая\b", re.IGNORECASE), ","),
    (re.compile(r"\bкома\b", re.IGNORECASE), ","),
    (re.compile(r"\bcolon\b", re.IGNORECASE), ":"),
    (re.compile(r"\bдвоеточие\b", re.IGNORECASE), ":"),
    (re.compile(r"\bдвокрапка\b", re.IGNORECASE), ":"),
    (re.compile(r"\bsemicolon\b", re.IGNORECASE), ";"),
    (re.compile(r"\btab\b", re.IGNORECASE), "\t"),
    (re.compile(r"\bтабуляция\b", re.IGNORECASE), "\t"),
    (re.compile(r"\bтабуляція\b", re.IGNORECASE), "\t"),
]


def process_dictation_text(text: str) -> str:
    """Replace special voice commands with their character equivalents.

    The function processes the raw STT transcription before it is typed.
    Multi-word commands are matched first so "точка с запятой" resolves
    to ";" rather than replacing "точка" alone with ".".
    """
    result = text
    for pattern, replacement in _SPECIAL_COMMANDS:
        result = pattern.sub(replacement, result)
    # Clean up stray spaces around inserted special characters
    result = re.sub(r'\s*\n\s*', '\n', result)
    result = re.sub(r'\s*\t\s*', '\t', result)
    result = re.sub(r'\s+([?!.,;:])', r'\1', result)
    return result


# ---------------------------------------------------------------------------
# Direct typing — bypasses the registry confirmation gate
# ---------------------------------------------------------------------------


async def type_dictation_text(text: str) -> None:
    """Type processed dictation text into the active window.

    Uses ``pyautogui.typewrite`` on a thread so the event loop is not
    blocked.  This is intentionally NOT routed through the registry
    because ``type_into_active_window`` carries ``requires_confirmation``,
    and prompting on every dictated word would make the feature unusable.
    """

    def _do_type() -> None:
        import pyautogui  # type: ignore[import-untyped]

        pyautogui.typewrite(text, interval=0.0)

    try:
        await asyncio.to_thread(_do_type)
    except Exception:
        log.warning("[dictation] typewrite failed", exc_info=True)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class DictationArgs(BaseModel):
    action: Literal["start", "stop"] = Field(
        description="'start' to begin dictation mode, 'stop' to end it.",
    )


class DictationTool:
    name: str = "dictation"
    description: str = (
        "Start or stop dictation mode.  While active, every spoken utterance "
        "is typed into the focused window as keystrokes instead of being "
        "interpreted by the LLM.  Special commands: 'new line', 'period', "
        "'comma', 'question mark', etc. are converted to their character "
        "equivalents.  Say 'stop dictation' to exit."
    )
    args_schema = DictationArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:start|begin)\s+dictation$",
            priority=200,
            args=lambda m: {"action": "start"},
        ),
        VoicePattern(
            regex=r"^(?:stop|end)\s+dictation$",
            priority=200,
            args=lambda m: {"action": "stop"},
        ),
        VoicePattern(
            regex=r"^dictate$",
            priority=200,
            args=lambda m: {"action": "start"},
        ),
        VoicePattern(
            regex=r"^stop\s+dictating$",
            priority=200,
            args=lambda m: {"action": "stop"},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:начни|начать)\s+диктовку$",
            priority=200,
            args=lambda m: {"action": "start"},
        ),
        VoicePattern(
            regex=r"^(?:закончи|стоп|останови|прекрати)\s+диктовк[уа]$",
            priority=200,
            args=lambda m: {"action": "stop"},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:почни|розпочни)\s+диктовку$",
            priority=200,
            args=lambda m: {"action": "start"},
        ),
        VoicePattern(
            regex=r"^(?:заверши|стоп|зупини|припини)\s+диктовк[уа]$",
            priority=200,
            args=lambda m: {"action": "stop"},
        ),
    )

    async def execute(self, args: DictationArgs) -> ToolResult:
        if args.action == "start":
            if is_dictation_active():
                return ToolResult(
                    success=True,
                    output="Dictation mode is already active, sir.",
                )
            _set_dictation(True)
            log.info("[dictation] mode activated")
            return ToolResult(
                success=True,
                output=(
                    "Dictation mode activated.  Everything you say will be "
                    "typed.  Say 'stop dictation' to exit."
                ),
            )
        else:  # stop
            if not is_dictation_active():
                return ToolResult(
                    success=True,
                    output="Dictation mode is not active, sir.",
                )
            _set_dictation(False)
            log.info("[dictation] mode deactivated")
            return ToolResult(
                success=True,
                output="Dictation mode stopped.  Back to normal, sir.",
            )
