"""Teach Jarvis a personal app alias by voice (Task 8b-3).

"запомни: баттл это Battle.net" -> PersonalAliasStore.learn("баттл",
"Battle.net"), so the next "открой баттл" resolves to the user's app even
when fuzzy matching / transliteration alone could not bridge the phonetic
gap ("клаут код" -> "Claude", etc.).

Voice-routed (bypasses the LLM) because it is a precise, closed-form
command. The alias is stored lowercased by the store; resolution is
case-insensitive. This tool only *records* the mapping — it never launches
anything.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.core.phrases import say
from jarvis.memory.personal_aliases import PersonalAliasStore
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# Separator between the alias and its target in a spoken "remember" command:
# RU "это"/"=", UK "це", EN "is"/"=", plus dash variants. Non-greedy alias
# capture so "X это Y это Z" binds the FIRST separator (alias = X).
_SEP = r"(?:\s+(?:это|—|-|=|це|is)\s+)"


class LearnAliasArgs(BaseModel):
    alias: str = Field(description="Spoken shortcut the user wants to teach, e.g. 'баттл'.")
    target: str = Field(description="App the alias should resolve to, e.g. 'Battle.net'.")


class LearnAliasTool:
    """Record a user-taught personal app alias (voice command)."""

    name: str = "learn_app_alias"
    description: str = (
        "Remembers a personal shortcut mapping one spoken name to an app, so "
        "future 'open <alias>' launches that app. Use when the user says, in "
        "any language, to remember that X means/is Y "
        "(RU: запомни X это Y / UK: запам'ятай X це Y / EN: remember X is Y). "
        "Records the mapping only; does not launch anything."
    )
    args_schema = LearnAliasArgs
    requires_confirmation: bool = False
    ends_turn_on_success: bool = True

    # priority 150: specific enough to beat open_app's catch-all ("open ...")
    # but it only matches the "remember <X> <sep> <Y>" shape anyway.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # RU: "запомни[,] [что] X это Y"
        VoicePattern(
            regex=r"^запомни,?\s+(?:что\s+)?(.+?)" + _SEP + r"(.+)$",
            priority=150,
            args=lambda m: {"alias": m.group(1), "target": m.group(2)},
        ),
        # UK: "запам'ятай[,] [що] X це Y"
        VoicePattern(
            regex=r"^запам['’]ятай,?\s+(?:що\s+)?(.+?)" + _SEP + r"(.+)$",
            priority=150,
            args=lambda m: {"alias": m.group(1), "target": m.group(2)},
        ),
        # EN: "remember[,] [that] X is Y"
        VoicePattern(
            regex=r"^remember,?\s+(?:that\s+)?(.+?)" + _SEP + r"(.+)$",
            priority=150,
            args=lambda m: {"alias": m.group(1), "target": m.group(2)},
        ),
    )

    def __init__(self, *, personal_aliases: PersonalAliasStore | None = None) -> None:
        self._personal_aliases = personal_aliases

    async def execute(self, args: LearnAliasArgs) -> ToolResult:
        alias = args.alias.strip()
        target = args.target.strip()
        if not alias or not target:
            return ToolResult(success=False, error="alias and target are required")
        if self._personal_aliases is None:
            return ToolResult(
                success=False, error="personal alias store is not available"
            )
        self._personal_aliases.learn(alias, target)
        log.info("[learn_alias] %r -> %r", alias, target)
        return ToolResult(
            success=True,
            output=say("alias_learned", alias=alias, target=target),
        )
