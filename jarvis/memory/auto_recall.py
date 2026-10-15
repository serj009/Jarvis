"""Automatic memory recall — tier-aware injection into LLM context (T4.4/T4.6).

Before each LLM call, search memory for facts relevant to the user's
utterance and inject them as a system message.  This gives the model
persistent cross-session context without the user explicitly asking
"вспомни".

Tier-aware behaviour (T4.6):
- HOT facts:     inject **original text** (full detail)
- WARM facts:    inject **condensed_text** (summary)
- COLD facts:    inject **condensed_text** prefixed with "[old memory]"
- ARCHIVE facts: **NOT injected** — only accessible via explicit RecallTool

Any matched fact (hot/warm/cold) is **promoted back to HOT** via
``memory_store.promote_to_hot()`` — this ensures that actively-referenced
memories never sink to lower tiers.

Preference-aware injection (T4.7):
- Facts tagged with behavioral preference tags (auto_behavior,
  auto_negative_pref, auto_tool_pref, auto_setting_pref) are injected
  BEFORE regular memories under a ``[User preferences:]`` header.
- These are treated as user instructions, not just context.

Design:
- Runs on every LLM call (not pattern-layer hits — those are instant)
- Uses MemoryStore.search + SmartSearch ranking with the user's utterance
- Top 3-5 results injected as a system message after the main prompt
- Latency: ~10-50 ms for FTS5, ~100 ms with FAISS (acceptable before
  LLM inference which is 1-5 s)
- Empty results → no injection (zero overhead on new topics)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from jarvis.memory.smart_search import SmartSearch

if TYPE_CHECKING:
    from jarvis.memory.store import Fact, MemoryStore
    from jarvis.memory.relations import RelationStore

log = logging.getLogger(__name__)


# Patterns that indicate the user is asking about past session activity.
# When matched, session_summary facts get an extra search pass so they
# surface even when FTS5 keyword overlap is poor.
_SESSION_HISTORY_PATTERN: re.Pattern[str] = re.compile(
    r"(?:"
    # EN
    r"what did we (?:do|discuss|talk about)|last (?:session|time|conversation)"
    r"|yesterday|earlier today"
    # RU
    r"|что мы (?:делали|обсуждали|обговаривали)"
    r"|(?:вчера|прошл(?:ый|ая|ое) (?:раз|сессия|разговор))"
    # UK
    r"|що ми (?:робили|обговорювали)"
    r"|(?:вчора|минул(?:ий|а|е) (?:раз|сесія|розмова))"
    r")",
    re.IGNORECASE,
)

# Tags that indicate a user preference (behavioral instruction).
# These are injected BEFORE regular memories with a special header
# so the LLM treats them as user instructions, not just context.
_PREFERENCE_TAGS: frozenset[str] = frozenset(
    {"auto_behavior", "auto_negative_pref", "auto_tool_pref", "auto_setting_pref"},
)


def _text_for_injection(fact: Fact) -> str | None:
    """Return the text to inject for *fact* based on its tier.

    - hot   → original text
    - warm  → condensed_text (falls back to original)
    - cold  → condensed_text prefixed with "[old memory]"
    - archive → None (never injected automatically)
    """
    tier = getattr(fact, "tier", "hot") or "hot"

    if tier == "archive":
        return None

    if tier == "hot":
        return fact.text

    # warm / cold — use condensed_text with fallback to original
    condensed = getattr(fact, "condensed_text", None) or fact.text

    if tier == "cold":
        return f"[old memory] {condensed}"

    # warm
    return condensed


def _is_preference_fact(fact: Fact) -> bool:
    """Return True if *fact* carries a preference tag."""
    fact_tags = set(getattr(fact, "tags", []) or [])
    return bool(fact_tags & _PREFERENCE_TAGS)


async def get_relevant_memories(
    memory_store: MemoryStore,
    utterance: str,
    *,
    max_results: int = 5,
    min_score: float = 0.3,
    language: str = "en",
    relation_store: RelationStore | None = None,
    max_total: int = 10,
    searcher: SmartSearch | None = None,
) -> str | None:
    """Search memory for facts relevant to *utterance*.

    Returns a formatted string for injection into LLM context,
    or ``None`` if no relevant memories found.

    Tier behaviour:
    - Search checks **original text** across ALL tiers (hot/warm/cold).
    - Archive facts are excluded from results.
    - Each matched fact is promoted back to HOT.
    - Injection text varies by tier (see ``_text_for_injection``).

    Preference boost:
    - Facts with preference tags (behavior, negative_pref, tool_pref,
      setting_pref) are placed at the top under "[User preferences:]"
      so the LLM treats them as instructions.

    Parameters
    ----------
    memory_store:
        The open MemoryStore instance.
    utterance:
        The user's current transcription / text input.
    max_results:
        Maximum number of memories to inject.
    min_score:
        Minimum SmartSearch score (0.0-1.0) to consider relevant.
    language:
        Current UI / speech language (for SmartSearch language boost).
    relation_store:
        Optional RelationStore for graph-based fact expansion.
    max_total:
        Hard cap on total facts (primary + related). Default 10.
    searcher:
        Optional pre-configured SmartSearch instance.  When ``None``,
        a default keyword-only searcher is used.
    """
    if not memory_store.is_open:
        return None
    if not utterance or not utterance.strip():
        return None

    try:
        # 1. Fetch candidates via FTS5 / LIKE (fast, ~10-50 ms)
        #    FTS5 defaults to implicit AND for multi-word queries, which
        #    is too strict for memory recall ("Chrome Steam Stalker"
        #    should find facts about Chrome OR Steam OR Stalker).
        words = utterance.strip().split()
        search_query = " OR ".join(words) if len(words) > 1 else utterance

        raw_facts = await memory_store.search(search_query, limit=20)

        # 1b. Session-history boost: when the user asks about past
        # activity ("what did we do yesterday?"), pull in recent
        # session_summary facts even if FTS5 didn't find them via
        # keyword overlap.
        if _SESSION_HISTORY_PATTERN.search(utterance):
            session_facts = await memory_store.search(
                "Session", category="session", tag="session_summary", limit=5,
            )
            existing_ids = {f.id for f in raw_facts}
            for sf in session_facts:
                if sf.id not in existing_ids:
                    raw_facts.append(sf)

        if not raw_facts:
            return None

        # 2. Rank with SmartSearch (time decay, frequency, semantic, etc.)
        _searcher = searcher or SmartSearch()
        scored = _searcher.rank(
            raw_facts,
            utterance,
            language=language,
            limit=max_results,
        )

        # 3. Filter by minimum relevance score
        relevant = [s for s in scored if s.score >= min_score]
        if not relevant:
            return None

        # 3b. Expand with related facts via relation graph
        if relation_store is not None:
            try:
                primary_ids = {s.fact.id for s in relevant}
                related_facts: list[Fact] = []
                seen_ids = set(primary_ids)
                budget = max_total - len(relevant)

                for s in relevant:
                    if budget <= 0:
                        break
                    edges = relation_store.get_related(s.fact.id, depth=1)
                    for edge in edges:
                        if budget <= 0:
                            break
                        # Extract the related fact ID (the "other" side)
                        other_id = edge.get("fact_id_to") or ""
                        if other_id == s.fact.id:
                            other_id = edge.get("fact_id_from") or ""
                        if not other_id or other_id in seen_ids:
                            continue
                        seen_ids.add(other_id)
                        fact = await memory_store.get(other_id)
                        if fact is not None and getattr(fact, "tier", "hot") != "archive":
                            related_facts.append(fact)
                            budget -= 1

                log.debug(
                    "[auto-recall] expanded %d related facts from relation graph",
                    len(related_facts),
                )
            except Exception:
                related_facts = []
                log.debug("[auto-recall] relation expansion failed", exc_info=True)
        else:
            related_facts = []

        # 4. Build injection lines (tier-aware) and promote matched facts.
        #    Preferences go into a separate list so they appear first.
        pref_lines: list[str] = []
        memory_lines: list[str] = []
        promote_ids: list[str] = []

        for s in relevant:
            text = _text_for_injection(s.fact)
            if text is None:
                continue  # archive — skip

            # Classify: preference vs regular memory
            if _is_preference_fact(s.fact):
                pref_lines.append(f"- {text}")
            else:
                memory_lines.append(f"- {text}")

            # Queue promotion for non-hot facts
            tier = getattr(s.fact, "tier", "hot") or "hot"
            if tier != "hot":
                promote_ids.append(s.fact.id)

        # 4b. Append related facts (always into memory_lines)
        for fact in related_facts:
            text = _text_for_injection(fact)
            if text is None:
                continue
            memory_lines.append(f"- [related] {text}")
            # Also promote related non-hot facts
            tier = getattr(fact, "tier", "hot") or "hot"
            if tier != "hot":
                promote_ids.append(fact.id)

        # Nothing injectable (all archive)?
        if not pref_lines and not memory_lines:
            return None

        # Build final injection: preferences first (they are user instructions),
        # then regular memories.
        lines: list[str] = []
        if pref_lines:
            lines.append("[User preferences:]")
            lines.extend(pref_lines)
        if memory_lines:
            lines.append("[Relevant memories from past conversations:]")
            lines.extend(memory_lines)

        # 5. Promote matched facts back to HOT (fire-and-forget style)
        for fid in promote_ids:
            try:
                await memory_store.promote_to_hot(fid)
                log.debug("[auto-recall] promoted fact %s back to HOT", fid)
            except Exception:
                log.debug("[auto-recall] promote failed for %s", fid, exc_info=True)

        result = "\n".join(lines)
        log.debug(
            "[auto-recall] injecting %d memories (%d prefs + %d regular, "
            "top score=%.2f, promoted=%d)",
            len(pref_lines) + len(memory_lines),
            len(pref_lines),
            len(memory_lines),
            relevant[0].score,
            len(promote_ids),
        )
        return result

    except Exception:
        log.warning("[auto-recall] search failed (non-fatal)", exc_info=True)
        return None
