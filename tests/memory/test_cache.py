"""Tests for jarvis.memory.cache."""

from __future__ import annotations

import time

from jarvis.memory.cache import IntentCache, ResponseCache, ShortTermMemory


class TestResponseCache:
    def test_put_and_get(self) -> None:
        cache = ResponseCache(max_size=10, ttl_seconds=60)
        cache.put("hello", "world")
        assert cache.get("hello") == "world"

    def test_miss_returns_none(self) -> None:
        cache = ResponseCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self) -> None:
        cache = ResponseCache(ttl_seconds=0.01)
        cache.put("key", "value")
        time.sleep(0.02)
        assert cache.get("key") is None

    def test_lru_eviction(self) -> None:
        cache = ResponseCache(max_size=2, ttl_seconds=60)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")  # Should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == "2"
        assert cache.get("c") == "3"

    def test_normalization(self) -> None:
        cache = ResponseCache()
        cache.put("  Hello  WORLD  ", "response")
        assert cache.get("hello world") == "response"

    def test_clear(self) -> None:
        cache = ResponseCache()
        cache.put("key", "value")
        cache.clear()
        assert cache.size == 0

    def test_hit_count(self) -> None:
        cache = ResponseCache()
        cache.put("q", "a")
        cache.get("q")
        cache.get("q")
        # Internal state - just verify it doesn't crash
        assert cache.size == 1


class TestIntentCache:
    def test_learn_and_get(self) -> None:
        cache = IntentCache()
        cache.learn("open youtube", "open_url")
        assert cache.get("open youtube") == "open_url"

    def test_miss_returns_none(self) -> None:
        cache = IntentCache()
        assert cache.get("unknown command") is None

    def test_forget(self) -> None:
        cache = IntentCache()
        cache.learn("open youtube", "open_url")
        cache.forget("open youtube")
        assert cache.get("open youtube") is None

    def test_normalization(self) -> None:
        cache = IntentCache()
        cache.learn("Open  YouTube", "open_url")
        assert cache.get("open youtube") == "open_url"

    def test_save_and_load(self, tmp_path) -> None:
        path = tmp_path / "intents.json"
        cache = IntentCache(path=path)
        cache.learn("open spotify", "open_url")
        cache.save()

        cache2 = IntentCache(path=path)
        cache2.load()
        assert cache2.get("open spotify") == "open_url"

    def test_clear(self) -> None:
        cache = IntentCache()
        cache.learn("a", "b")
        cache.clear()
        assert cache.size == 0


class TestShortTermMemory:
    def test_add_and_recent(self) -> None:
        stm = ShortTermMemory(max_turns=5)
        stm.add("Hello", "Hi, sir.")
        turns = stm.recent()
        assert len(turns) == 1
        assert turns[0].user == "Hello"

    def test_max_capacity(self) -> None:
        stm = ShortTermMemory(max_turns=2)
        stm.add("Q1", "A1")
        stm.add("Q2", "A2")
        stm.add("Q3", "A3")  # Should evict Q1
        assert stm.size == 2
        turns = stm.recent()
        assert turns[0].user == "Q2"

    def test_as_context_string(self) -> None:
        stm = ShortTermMemory()
        stm.add("What time?", "3 PM")
        stm.add("Thanks", "You're welcome")
        ctx = stm.as_context_string()
        assert "User: What time?" in ctx
        assert "Jarvis: 3 PM" in ctx
        assert "User: Thanks" in ctx

    def test_empty_context(self) -> None:
        stm = ShortTermMemory()
        assert stm.as_context_string() == ""
        assert stm.is_empty

    def test_clear(self) -> None:
        stm = ShortTermMemory()
        stm.add("Q", "A")
        stm.clear()
        assert stm.is_empty
