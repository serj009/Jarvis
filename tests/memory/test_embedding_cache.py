"""Tests for jarvis.memory.embedding_cache (EmbeddingCache).

Tests LRU eviction, hit rate tracking, get_or_compute, and cache stats.
"""

from __future__ import annotations

import pytest

from jarvis.memory.embedding_cache import EmbeddingCache


class TestEmbeddingCache:
    @pytest.fixture()
    def cache(self) -> EmbeddingCache:
        return EmbeddingCache(max_size=5)

    def test_put_and_get(self, cache) -> None:
        vec = [1.0, 2.0, 3.0]
        cache.put("hello world", vec)
        result = cache.get("hello world")
        assert result == vec

    def test_get_miss_returns_none(self, cache) -> None:
        assert cache.get("nonexistent") is None

    def test_case_insensitive_keys(self, cache) -> None:
        cache.put("Hello World", [1.0, 2.0])
        result = cache.get("hello world")
        assert result == [1.0, 2.0]

    def test_whitespace_normalized(self, cache) -> None:
        cache.put("  hello  ", [1.0])
        result = cache.get("hello")
        assert result == [1.0]

    def test_lru_eviction(self) -> None:
        cache = EmbeddingCache(max_size=3)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.put("c", [3.0])
        cache.put("d", [4.0])  # Should evict "a"

        assert cache.get("a") is None  # Evicted
        assert cache.get("b") == [2.0]
        assert cache.get("d") == [4.0]

    def test_lru_access_refreshes(self) -> None:
        cache = EmbeddingCache(max_size=3)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.put("c", [3.0])

        # Access "a" to move it to end (most recent)
        cache.get("a")

        cache.put("d", [4.0])  # Should evict "b" (least recently used)

        assert cache.get("a") == [1.0]  # Still alive (accessed recently)
        assert cache.get("b") is None   # Evicted
        assert cache.get("c") == [3.0]
        assert cache.get("d") == [4.0]

    def test_hit_rate_tracking(self) -> None:
        cache = EmbeddingCache(max_size=10)
        cache.put("x", [1.0])

        cache.get("x")   # Hit
        cache.get("x")   # Hit
        cache.get("y")   # Miss

        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "67%"

    def test_hit_rate_zero(self) -> None:
        cache = EmbeddingCache()
        stats = cache.get_stats()
        assert stats["hit_rate"] == "0%"

    def test_get_or_compute_miss(self, cache) -> None:
        def compute(text: str) -> list[float]:
            return [float(len(text))]

        result = cache.get_or_compute("hello", compute)
        assert result == [5.0]
        assert cache.size == 1

    def test_get_or_compute_hit(self, cache) -> None:
        cache.put("hello", [99.0])
        call_count = 0

        def compute(text: str) -> list[float]:
            nonlocal call_count
            call_count += 1
            return [float(len(text))]

        result = cache.get_or_compute("hello", compute)
        assert result == [99.0]  # From cache, not recomputed
        assert call_count == 0   # compute_fn was never called

    def test_clear(self) -> None:
        cache = EmbeddingCache(max_size=10)
        cache.put("a", [1.0])
        cache.put("b", [2.0])
        cache.get("a")  # Hit
        cache.get("z")  # Miss

        cache.clear()

        assert cache.size == 0
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    def test_size_property(self) -> None:
        cache = EmbeddingCache(max_size=10)
        assert cache.size == 0
        cache.put("a", [1.0])
        assert cache.size == 1
        cache.put("b", [2.0])
        assert cache.size == 2

    def test_get_stats_structure(self) -> None:
        cache = EmbeddingCache(max_size=100)
        stats = cache.get_stats()
        assert "size" in stats
        assert "max_size" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
        assert stats["max_size"] == 100

    def test_large_vectors(self, cache) -> None:
        """Works with realistic 384-dim vectors."""
        vec = [float(i) for i in range(384)]
        cache.put("test sentence", vec)
        result = cache.get("test sentence")
        assert result is not None
        assert len(result) == 384
        assert result[0] == 0.0
        assert result[383] == 383.0

    def test_overwrite_same_key(self, cache) -> None:
        """Putting the same text again overwrites the vector."""
        cache.put("text", [1.0])
        cache.put("text", [2.0])
        assert cache.get("text") == [2.0]
        assert cache.size == 1  # No duplicate entries.
