"""EmbeddingCache: LRU cache for embedding vectors.

Embedding models are slow (~10-50ms per query). When the same text is
encoded multiple times (common during search + re-ranking), this cache
returns the pre-computed vector in ~0.01ms.

Uses MD5 hash of normalized text as the cache key and OrderedDict for
LRU eviction. Pure in-memory — no disk persistence needed since vectors
can be recomputed.

Usage:
    cache = EmbeddingCache(max_size=2000)
    vec = cache.get_or_compute("hello world", model.encode)
    stats = cache.get_stats()  # {"size": 1, "hits": 0, "misses": 1, ...}
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from typing import Callable, Optional

log = logging.getLogger(__name__)


class EmbeddingCache:
    """LRU cache for embedding vectors.

    Thread-safe for single-threaded asyncio use (GIL protects OrderedDict).
    For true multi-threaded use, wrap with a lock.
    """

    def __init__(self, max_size: int = 2000) -> None:
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(text: str) -> str:
        """Compute cache key from normalized text."""
        return hashlib.md5(text.lower().strip().encode("utf-8")).hexdigest()

    def get(self, text: str) -> Optional[list[float]]:
        """Get a cached embedding vector, or None if not cached."""
        key = self._key(text)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, text: str, embedding: list[float]) -> None:
        """Store an embedding vector in the cache."""
        key = self._key(text)
        # Evict oldest entries if at capacity
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[key] = embedding

    def get_or_compute(
        self, text: str, compute_fn: Callable[[str], list[float]]
    ) -> list[float]:
        """Get from cache or compute + store.

        Args:
            text: The text to embed.
            compute_fn: Function that takes text and returns embedding vector.

        Returns:
            The embedding vector (from cache or freshly computed).
        """
        cached = self.get(text)
        if cached is not None:
            return cached
        result = compute_fn(text)
        self.put(text, result)
        return result

    def clear(self) -> None:
        """Clear all cached embeddings and reset stats."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def size(self) -> int:
        """Number of cached embeddings."""
        return len(self._cache)

    def get_stats(self) -> dict:
        """Return cache statistics including hit rate."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total:.0%}" if total > 0 else "0%",
        }
