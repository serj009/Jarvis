"""Tiered memory management — hot/warm/cold/archive lifecycle (T4.6).

Runs periodically (at startup and every N hours in background) to:
1. Move facts between tiers based on ``last_accessed_at`` age
2. Condense text when moving to lower tiers
3. Never delete — only compress and archive

Tier transitions (demotion):
  HOT  → WARM:    last_accessed_at > 30 days ago
  WARM → COLD:    last_accessed_at > 90 days ago
  COLD → ARCHIVE: last_accessed_at > 14 months (~425 days) ago

Promotion (tier UP):
  Any access (recall / auto-recall match / explicit get) promotes the
  fact back to HOT and resets ``last_accessed_at``.  A fact mentioned
  after 6 months in COLD instantly becomes HOT again.

Condensation rules:
  HOT → WARM:     keep first 100 chars + ellipsis
  WARM → COLD:    keep first 50 chars only
  COLD → ARCHIVE: keep tags prefix + first 30 chars

The **original text is NEVER modified** — ``condensed_text`` is a
separate column.  ``search()`` always checks original text; condensation
only affects what's injected into LLM context by auto-recall.

Permanent facts (``is_permanent=True``) are excluded from tier
transitions — they stay HOT forever.

Usage:
    mgr = TierManager(memory_store)
    counts = await mgr.run_maintenance()
    # {'hot_to_warm': 5, 'warm_to_cold': 2, 'cold_to_archive': 0}
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import Fact, MemoryStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier transition thresholds (days since last access)
# ---------------------------------------------------------------------------

HOT_TO_WARM_DAYS = 30
WARM_TO_COLD_DAYS = 90
COLD_TO_ARCHIVE_DAYS = 425  # ~14 months


class TierManager:
    """Manage fact lifecycle across memory tiers.

    Parameters
    ----------
    memory_store:
        An open ``MemoryStore`` instance (provides ``get_facts_by_tier_and_age``,
        ``update_tier``, ``promote_to_hot``).
    """

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    # -- public API ---------------------------------------------------------

    async def run_maintenance(self) -> dict[str, int]:
        """Run tier demotion transitions.  Returns counts of facts moved.

        Safe to call frequently — only facts past their threshold are moved.
        Non-blocking: runs in ``asyncio.to_thread`` inside the store methods.
        """
        counts = {"hot_to_warm": 0, "warm_to_cold": 0, "cold_to_archive": 0}

        now = datetime.now(UTC)

        # HOT → WARM  (last accessed > 30 days ago)
        hot_cutoff = now - timedelta(days=HOT_TO_WARM_DAYS)
        hot_facts = await self._store.get_facts_by_tier_and_age("hot", hot_cutoff)
        for fact in hot_facts:
            condensed = self._condense_for_warm(fact.text)
            await self._store.update_tier(fact.id, "warm", condensed)
            counts["hot_to_warm"] += 1

        # WARM → COLD  (last accessed > 90 days ago)
        warm_cutoff = now - timedelta(days=WARM_TO_COLD_DAYS)
        warm_facts = await self._store.get_facts_by_tier_and_age("warm", warm_cutoff)
        for fact in warm_facts:
            condensed = self._condense_for_cold(fact.text)
            await self._store.update_tier(fact.id, "cold", condensed)
            counts["warm_to_cold"] += 1

        # COLD → ARCHIVE  (last accessed > 14 months ago)
        cold_cutoff = now - timedelta(days=COLD_TO_ARCHIVE_DAYS)
        cold_facts = await self._store.get_facts_by_tier_and_age("cold", cold_cutoff)
        for fact in cold_facts:
            condensed = self._condense_for_archive(fact.text, fact.tags)
            await self._store.update_tier(fact.id, "archive", condensed)
            counts["cold_to_archive"] += 1

        if any(counts.values()):
            log.info("tier maintenance completed: %s", counts)
        else:
            log.debug("tier maintenance: nothing to move")

        return counts

    # -- condensation rules -------------------------------------------------

    @staticmethod
    def _condense_for_warm(text: str) -> str:
        """HOT → WARM: keep first 100 characters."""
        if len(text) <= 100:
            return text
        return text[:97] + "..."

    @staticmethod
    def _condense_for_cold(text: str) -> str:
        """WARM → COLD: keep first 50 characters."""
        if len(text) <= 50:
            return text
        return text[:47] + "..."

    @staticmethod
    def _condense_for_archive(text: str, tags: list[str]) -> str:
        """COLD → ARCHIVE: tags prefix + first 30 characters."""
        tag_str = ", ".join(tags[:5]) if tags else ""
        short = text[:30] + "..." if len(text) > 30 else text
        if tag_str:
            return f"[{tag_str}] {short}"
        return short
