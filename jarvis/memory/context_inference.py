"""Contextual inference from memory — connecting facts for smarter responses (T4.6).

Resolves implicit references like "that game", "the site we opened",
"launch what we were playing" by combining recent tool-call history
(ToolExampleStore) with memory facts (MemoryStore).

Strategy:
  1. Detect implicit-reference patterns in the utterance (regex)
  2. Map the pattern to a tool category (game → launch_*, site → open_url, etc.)
  3. Look up the most recent successful call for that category
  4. Fall back to MemoryStore search for the entity

This is NOT LLM-based — it's pure heuristic for speed (<5 ms).
If no inference is possible, returns ``None`` and the LLM handles
the utterance as-is.

Usage:
    ci = ContextInference(memory_store, tool_example_store)
    enriched = await ci.infer_implicit_reference("запусти ту игру")
    # → "запусти ту игру (inferred: Mafia 2)"

    recent = ci.get_recent_tool_activity("launch_steam_game", days=7)
    # → [{"utterance": "запусти мафию", "args": {"game": "Mafia 2"}, ...}]
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore
    from jarvis.memory.tool_examples import ToolExampleStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Implicit-reference patterns → tool category mapping
# ---------------------------------------------------------------------------
# Each entry: (compiled_regex, list_of_tool_name_prefixes, entity_label)

_IMPLICIT_PATTERNS: list[tuple[re.Pattern[str], list[str], str]] = [
    # Games: "ту игру", "that game", "ту гру"
    (
        re.compile(
            r"(?:ту|эту|тую)\s+(?:игр[уы]|гр[уы])|that\s+game|the\s+game",
            re.IGNORECASE,
        ),
        ["launch_steam_game", "launch_game"],
        "game",
    ),
    # Apps/programs: "ту программу", "that app", "ту прогу"
    (
        re.compile(
            r"(?:ту|эту)\s+(?:программ[уы]|прог[уы]|аппку|додаток)|that\s+(?:app|program)",
            re.IGNORECASE,
        ),
        ["open_app", "launch_app"],
        "app",
    ),
    # Websites: "тот сайт", "that site", "that page"
    (
        re.compile(
            r"(?:тот|этот)\s+(?:сайт|сторінк[уы]|страниц[уы])|that\s+(?:site|page|website|url)",
            re.IGNORECASE,
        ),
        ["open_url", "open_browser"],
        "url",
    ),
    # Music: "ту песню", "that song", "that track"
    (
        re.compile(
            r"(?:ту|эту)\s+(?:песн[юи]|пісн[юі]|музык[уы]|трек)|that\s+(?:song|track|music)",
            re.IGNORECASE,
        ),
        ["play_music", "open_spotify"],
        "music",
    ),
    # Generic "what we were doing": "что мы делали", "what we were playing"
    (
        re.compile(
            r"что\s+мы\s+(?:делали|проходили|играли|смотрели)"
            r"|what\s+we\s+were\s+(?:doing|playing|watching)",
            re.IGNORECASE,
        ),
        [],  # search all tools
        "activity",
    ),
]


class ContextInference:
    """Resolve implicit references using tool history and memory.

    Parameters
    ----------
    memory_store:
        Open MemoryStore for fact lookups.
    tool_example_store:
        ToolExampleStore with recorded successful calls.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        tool_example_store: ToolExampleStore | None = None,
    ) -> None:
        self._memory = memory_store
        self._examples = tool_example_store

    # -- public API ---------------------------------------------------------

    def get_recent_tool_activity(
        self, tool_name: str, *, days: int = 7,
    ) -> list[dict]:
        """Get recent successful calls for *tool_name*.

        Filters tool_example_store entries by ``last_used`` timestamp.
        Falls back to all entries if no ``last_used`` field is present.
        """
        if self._examples is None:
            return []

        examples = self._examples.data.get(tool_name, [])
        if not examples:
            return []

        cutoff = datetime.now(UTC) - timedelta(days=days)
        recent = []
        for ex in examples:
            last_used = ex.get("last_used")
            if last_used:
                try:
                    ts = datetime.fromisoformat(last_used)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass  # can't parse → include it
            recent.append(ex)

        # Sort by count descending (most used = most likely target)
        recent.sort(key=lambda x: x.get("count", 0), reverse=True)
        return recent

    async def infer_implicit_reference(self, utterance: str) -> str | None:
        """Try to resolve implicit references in *utterance*.

        Returns an enriched string like:
            "запусти ту игру (inferred: Mafia 2)"
        or ``None`` if no inference is possible.
        """
        if not utterance or not utterance.strip():
            return None

        for pattern, tool_prefixes, entity_label in _IMPLICIT_PATTERNS:
            if not pattern.search(utterance):
                continue

            # 1. Try tool examples first (most reliable — actual past calls)
            inferred = self._infer_from_tools(tool_prefixes)
            if inferred:
                log.debug(
                    "[context-inference] resolved '%s' → '%s' from tool history",
                    entity_label, inferred,
                )
                return f"{utterance} (inferred: {inferred})"

            # 2. Fall back to memory search
            inferred = await self._infer_from_memory(entity_label)
            if inferred:
                log.debug(
                    "[context-inference] resolved '%s' → '%s' from memory",
                    entity_label, inferred,
                )
                return f"{utterance} (inferred: {inferred})"

        return None

    # -- private helpers ----------------------------------------------------

    def _infer_from_tools(self, tool_prefixes: list[str]) -> str | None:
        """Look up the most recent / most frequent call for matching tools."""
        if self._examples is None:
            return None

        best: dict | None = None
        best_count = -1

        for tool_name, examples in self._examples.data.items():
            # If prefixes specified, filter by them
            if tool_prefixes:
                if not any(tool_name.startswith(p) for p in tool_prefixes):
                    continue

            for ex in examples:
                count = ex.get("count", 0)
                # Prefer entries with last_used (recently used)
                last_used = ex.get("last_used")
                # Boost recent entries
                recency_bonus = 0
                if last_used:
                    try:
                        ts = datetime.fromisoformat(last_used)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                        age_days = (datetime.now(UTC) - ts).total_seconds() / 86400
                        if age_days < 7:
                            recency_bonus = 100  # strong boost for last week
                        elif age_days < 30:
                            recency_bonus = 10
                    except (ValueError, TypeError):
                        pass

                effective_score = count + recency_bonus
                if effective_score > best_count:
                    best_count = effective_score
                    best = ex

        if best is None:
            return None

        # Extract the entity name from args
        args = best.get("args", {})
        # Try common arg names
        for key in ("game", "game_name", "app_name", "app", "url", "name",
                     "song", "track", "query"):
            if key in args and args[key]:
                return str(args[key])

        # Fall back to first arg value
        for val in args.values():
            if val and isinstance(val, str):
                return val

        return None

    async def _infer_from_memory(self, entity_label: str) -> str | None:
        """Search memory for the most recent fact matching *entity_label*."""
        if not self._memory.is_open:
            return None

        try:
            # Map entity labels to search categories
            category_map = {
                "game": "games",
                "app": "software",
                "url": "general",
                "music": "preference",
                "activity": None,  # search all
            }
            category = category_map.get(entity_label)
            facts = await self._memory.search(
                entity_label, category=category, limit=3,
            )
            if facts:
                # Return the most recently updated fact's text
                return facts[0].text
        except Exception:
            log.debug("[context-inference] memory search failed", exc_info=True)

        return None
