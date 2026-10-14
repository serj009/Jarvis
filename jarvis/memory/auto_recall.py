"""Automatic memory recall — inject relevant memories into LLM context.

Before each LLM call, search memory for facts relevant to the user's
utterance and inject them as a system message.  This gives the model
persistent cross-session context without the user explicitly asking
"вспомни".

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
from typing import TYPE_CHECKING

from jarvis.memory.smart_search import SmartSearch

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)


async def get_relevant_memories(
    memory_store: MemoryStore,
    utterance: str,
    *,
    max_results: int = 5,
    min_score: float = 0.3,
    language: str = "en",
    searcher: SmartSearch | None = None,
) -> str | None:
    """Search memory for facts relevant to *utterance*.

    Returns a formatted string for injection into LLM context,
    or ``None`` if no relevant memories found.

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
        raw_facts = await memory_store.search(utterance, limit=20)
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

        # 4. Format for LLM injection
        lines = ["[Relevant memories from past conversations:]"]
        for s in relevant:
            lines.append(f"- {s.fact.text}")

        result = "\n".join(lines)
        log.debug(
            "[auto-recall] injecting %d memories (top score=%.2f)",
            len(relevant),
            relevant[0].score,
        )
        return result

    except Exception:
        log.warning("[auto-recall] search failed (non-fatal)", exc_info=True)
        return None
