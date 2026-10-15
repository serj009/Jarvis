"""Tell the user the current time and/or date."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel


# Month names in genitive case (required by RU/UK date format).
_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_DAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]

_MONTHS_UK = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]
_DAYS_UK = [
    "понеділок", "вівторок", "середа", "четвер",
    "п'ятниця", "субота", "неділя",
]


class GetTimeTool:
    """Return the current local time and date, formatted in the user's
    language (EN / RU / UK).  No arguments needed."""

    name: str = "get_time"
    description: str = (
        "Tell the current time and date. Use when the user asks "
        "what time it is or today's date. "
        "(RU: сколько времени, который час / UK: котра година, скільки часу)"
    )
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ

    # ── voice patterns ────────────────────────────────────────────
    # Priority 80: high priority, no parameters, exact match.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+the\s+)?time\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^what\s+time\s+is\s+it\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?)?(?:today(?:'s\s+|\s+))?date\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^what\s+day\s+is\s+(?:it\s+)?today\??$",
            priority=80,
        ),
        # RU
        VoicePattern(
            regex=r"^(?:который\s+час|сколько\s+(?:сейчас\s+)?времен\w*)\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^(?:какое?\s+(?:сейчас\s+)?(?:время|час))\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^(?:какое?\s+(?:сегодня\s+)?число|какой\s+(?:сегодня\s+)?день)\??$",
            priority=80,
        ),
        # UK
        VoicePattern(
            regex=r"^(?:котра\s+година|скільки\s+(?:зараз\s+)?час[уі]?)\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^(?:яка?\s+(?:зараз\s+)?(?:час|година))\??$",
            priority=80,
        ),
        VoicePattern(
            regex=r"^(?:яке?\s+(?:сьогодні\s+)?число|який\s+(?:сьогодні\s+)?день)\??$",
            priority=80,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        now = datetime.now()

        from jarvis.core.phrases import reply_language

        lang = reply_language()

        if lang == "ru":
            text = (
                f"Сейчас {now.strftime('%H:%M')}, "
                f"{_DAYS_RU[now.weekday()]}, "
                f"{now.day} {_MONTHS_RU[now.month - 1]} {now.year}"
            )
        elif lang == "uk":
            text = (
                f"Зараз {now.strftime('%H:%M')}, "
                f"{_DAYS_UK[now.weekday()]}, "
                f"{now.day} {_MONTHS_UK[now.month - 1]} {now.year}"
            )
        else:
            # "It's 3:42 PM, Wednesday, October 14, 2026"
            text = now.strftime("It's %I:%M %p, %A, %B %d, %Y").replace(
                " 0", " "
            )

        return ToolResult(success=True, output=f"{text}, sir.")
