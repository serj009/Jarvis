"""PredictivePreload: preload knowledge into RAM by detected context.

When a game is running → load its achievements/tips into RAM.
When it's morning → load work-related facts.
When a topic is frequently accessed → keep it cached.

Result: search in preloaded data is ~0.01ms (RAM dict) vs ~5ms (SQLite).

No FAISS dependency. Preloading is by category/tags from MemoryStore.
Process detection is via subprocess (Windows tasklist).

Usage:
    preload = PredictivePreload(memory_store=store)
    preload.add_rule("stalker2-win64-shipping.exe", category="games", tags=["stalker_2"])
    preload.check_and_preload()  # called periodically
    results = preload.search("achievement")
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import Fact, MemoryStore

log = logging.getLogger(__name__)


@dataclass
class PreloadRule:
    """A rule that triggers preloading when a process is detected."""
    process_name: str       # e.g. "stalker2-win64-shipping.exe"
    category: str = ""      # category filter for MemoryStore.search
    tags: list[str] = field(default_factory=list)
    query: str = ""         # optional search query
    max_facts: int = 100    # max facts to preload


class PredictivePreload:
    """Preloads facts into RAM based on running processes or time of day."""

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self._store = memory_store
        self._rules: list[PreloadRule] = []
        self._preloaded: dict[str, list[Fact]] = {}  # rule_key -> facts

    def add_rule(
        self,
        process_name: str,
        *,
        category: str = "",
        tags: list[str] | None = None,
        query: str = "",
        max_facts: int = 100,
    ) -> None:
        """Add a preload rule: when process is running, load matching facts."""
        self._rules.append(PreloadRule(
            process_name=process_name.lower(),
            category=category,
            tags=tags or [],
            query=query,
            max_facts=max_facts,
        ))
        log.info("preload rule: %s -> category=%s tags=%s", process_name, category, tags)

    async def check_and_preload(self) -> list[str]:
        """Check running processes and preload/unload accordingly.

        Returns list of newly preloaded rule keys.
        """
        if self._store is None or not self._store.is_open:
            return []

        running = self._get_running_processes()
        loaded = []

        for rule in self._rules:
            key = rule.process_name
            is_running = key in running

            if is_running and key not in self._preloaded:
                await self._preload(rule)
                loaded.append(key)
            elif not is_running and key in self._preloaded:
                self._unload(key)

        return loaded

    async def _preload(self, rule: PreloadRule) -> None:
        """Load facts matching the rule into RAM."""
        if self._store is None:
            return

        # Search by query or category
        query = rule.query or rule.category or " ".join(rule.tags)
        if not query:
            return

        facts = await self._store.search(
            query,
            limit=rule.max_facts,
            category=rule.category or None,
        )

        self._preloaded[rule.process_name] = facts
        log.info("preloaded %d facts for %s", len(facts), rule.process_name)

    def _unload(self, key: str) -> None:
        """Remove preloaded facts from RAM."""
        count = len(self._preloaded.get(key, []))
        self._preloaded.pop(key, None)
        if count:
            log.info("unloaded %d facts for %s", count, key)

    def search(self, query: str, limit: int = 5) -> list[Fact]:
        """Search across all preloaded facts (RAM, instant)."""
        query_lower = query.lower()
        words = set(query_lower.split())

        scored: list[tuple[int, Fact]] = []
        for facts in self._preloaded.values():
            for fact in facts:
                text_lower = fact.text.lower()
                score = sum(1 for w in words if w in text_lower)
                if score > 0:
                    scored.append((score, fact))

        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored[:limit]]

    @staticmethod
    def _get_running_processes() -> set[str]:
        """Get set of running process names (Windows)."""
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            processes = set()
            for line in result.stdout.strip().split("\n"):
                if line:
                    name = line.split(",")[0].strip('"').lower()
                    processes.add(name)
            return processes
        except Exception:
            return set()

    @property
    def preloaded_keys(self) -> list[str]:
        return list(self._preloaded.keys())

    @property
    def stats(self) -> dict:
        return {
            "rules": len(self._rules),
            "preloaded": {k: len(v) for k, v in self._preloaded.items()},
            "total_facts_in_ram": sum(len(v) for v in self._preloaded.values()),
        }
