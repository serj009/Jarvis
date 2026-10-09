"""Memory caches: ResponseCache, IntentCache, ShortTermMemory.

Five cache layers from the memory_kit_v2 architecture, implemented as
lightweight in-memory structures with optional persistence.

1. **ResponseCache** — LRU+TTL cache for LLM responses. If the user asks
   the same question twice within TTL, skip the LLM entirely.

2. **IntentCache** — persists learned intent->action mappings. If "open
   youtube" was routed to open_url before, skip the LLM classification.
   Persisted to JSON so it survives restarts.

3. **ShortTermMemory** — last N conversation turns in RAM for quick context
   injection into the LLM system prompt. Not persisted.

4. **EmbeddingCache** — (stub) cache for computed embeddings. Will be
   activated when FAISS is added.

5. **PredictivePreload** — (stub) preloads relevant facts when a game or
   topic is detected. Will be activated with the game detection hook.

All caches are thread-safe for the audio loop (single-writer asyncio).
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. ResponseCache — LRU + TTL
# ---------------------------------------------------------------------------

@dataclass
class CachedResponse:
    """A cached LLM response."""
    query_hash: str
    response: str
    created_at: float
    hit_count: int = 0


class ResponseCache:
    """LRU cache with TTL for LLM responses.

    Skip the LLM if we've seen this exact query recently.

    Usage:
        cache = ResponseCache(max_size=100, ttl_seconds=300)
        cached = cache.get("what time is it")
        if cached:
            return cached  # Skip LLM
        response = await llm.generate(query)
        cache.put("what time is it", response)
    """

    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CachedResponse] = OrderedDict()

    def _normalize(self, query: str) -> str:
        """Normalize query for cache key."""
        return " ".join(query.lower().strip().split())

    def get(self, query: str) -> str | None:
        """Get cached response if exists and not expired."""
        key = self._normalize(query)
        entry = self._cache.get(key)
        if entry is None:
            return None

        # Check TTL
        if time.monotonic() - entry.created_at > self._ttl:
            del self._cache[key]
            return None

        entry.hit_count += 1
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        log.debug("response cache hit: %r (hits=%d)", key[:40], entry.hit_count)
        return entry.response

    def put(self, query: str, response: str) -> None:
        """Store a response in the cache."""
        key = self._normalize(query)
        self._cache[key] = CachedResponse(
            query_hash=key,
            response=response,
            created_at=time.monotonic(),
        )
        self._cache.move_to_end(key)
        # Evict oldest if over capacity
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# 2. IntentCache — persistent intent->action mapping
# ---------------------------------------------------------------------------

class IntentCache:
    """Maps known user phrases to tool actions, bypassing LLM classification.

    Persisted to a JSON file so learned intents survive restarts.

    Usage:
        cache = IntentCache(path=Path(".../intent_cache.json"))
        cache.load()
        action = cache.get("open youtube")  # -> "open_url" or None
        cache.learn("open youtube", "open_url")
        cache.save()
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._map: dict[str, str] = {}  # normalized_query -> tool_name

    def _normalize(self, query: str) -> str:
        return " ".join(query.lower().strip().split())

    def get(self, query: str) -> str | None:
        """Get cached tool name for a query, or None."""
        return self._map.get(self._normalize(query))

    def learn(self, query: str, tool_name: str) -> None:
        """Record that this query maps to this tool."""
        self._map[self._normalize(query)] = tool_name

    def forget(self, query: str) -> None:
        """Remove a learned mapping."""
        self._map.pop(self._normalize(query), None)

    def load(self) -> None:
        """Load from disk."""
        if self._path is None or not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                self._map = json.load(f)
            log.info("intent cache loaded: %d entries from %s", len(self._map), self._path)
        except Exception:
            log.warning("intent cache load failed", exc_info=True)

    def save(self) -> None:
        """Persist to disk."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as f:
                json.dump(self._map, f, ensure_ascii=False, indent=2)
        except Exception:
            log.warning("intent cache save failed", exc_info=True)

    @property
    def size(self) -> int:
        return len(self._map)

    def clear(self) -> None:
        self._map.clear()


# ---------------------------------------------------------------------------
# 3. ShortTermMemory — last N turns in RAM
# ---------------------------------------------------------------------------

@dataclass
class MemoryTurn:
    """A conversation turn in short-term memory."""
    user: str
    assistant: str
    timestamp: float = field(default_factory=time.monotonic)


class ShortTermMemory:
    """Ring buffer of recent conversation turns for context injection.

    The LLM's system prompt or conversation history can pull from this
    to have context about what was just discussed, even across wake-word
    resets (which clear the Conversation object).

    Usage:
        stm = ShortTermMemory(max_turns=10)
        stm.add("What's the weather?", "Sunny, 20 degrees.")
        context = stm.as_context_string()
        # -> "User: What's the weather?\nJarvis: Sunny, 20 degrees."
    """

    def __init__(self, max_turns: int = 10) -> None:
        self._max = max_turns
        self._turns: list[MemoryTurn] = []

    def add(self, user: str, assistant: str) -> None:
        """Add a turn. Evicts oldest if over capacity."""
        self._turns.append(MemoryTurn(user=user, assistant=assistant))
        if len(self._turns) > self._max:
            self._turns.pop(0)

    def recent(self, n: int | None = None) -> list[MemoryTurn]:
        """Get the last N turns (default: all)."""
        if n is None:
            return list(self._turns)
        return self._turns[-n:]

    def as_context_string(self, n: int | None = None) -> str:
        """Format recent turns as a string for LLM context."""
        turns = self.recent(n)
        if not turns:
            return ""
        lines = []
        for t in turns:
            lines.append(f"User: {t.user}")
            lines.append(f"Jarvis: {t.assistant}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._turns.clear()

    @property
    def size(self) -> int:
        return len(self._turns)

    @property
    def is_empty(self) -> bool:
        return len(self._turns) == 0
