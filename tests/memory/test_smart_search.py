"""Tests for jarvis.memory.smart_search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jarvis.memory.smart_search import SmartSearch
from jarvis.memory.store import Fact


def _fact(
    text: str,
    *,
    category: str = "general",
    language: str = "en",
    access_count: int = 0,
    updated_at: str | None = None,
    is_permanent: bool = False,
) -> Fact:
    now = datetime.now(UTC).isoformat()
    return Fact(
        id="test",
        text=text,
        category=category,
        language=language,
        access_count=access_count,
        updated_at=updated_at or now,
        created_at=now,
        is_permanent=is_permanent,
    )


class TestSmartSearch:
    def test_empty_facts_returns_empty(self) -> None:
        s = SmartSearch()
        assert s.rank([], "query") == []

    def test_exact_match_scores_higher(self) -> None:
        s = SmartSearch()
        facts = [
            _fact("The weather is sunny today"),
            _fact("I like pizza"),
        ]
        results = s.rank(facts, "weather")
        assert results[0].fact.text == "The weather is sunny today"
        assert results[0].score > results[1].score

    def test_frequency_boost(self) -> None:
        s = SmartSearch()
        facts = [
            _fact("Stalker 2 saves", access_count=0),
            _fact("Stalker 2 location", access_count=50),
        ]
        results = s.rank(facts, "stalker")
        # Higher access_count should score higher (all else equal)
        assert results[0].fact.text == "Stalker 2 location"

    def test_recency_boost(self) -> None:
        s = SmartSearch()
        old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        new_date = datetime.now(UTC).isoformat()
        facts = [
            _fact("Old fact about games", updated_at=old_date),
            _fact("New fact about games", updated_at=new_date),
        ]
        results = s.rank(facts, "games")
        assert results[0].fact.text == "New fact about games"

    def test_permanent_always_recent(self) -> None:
        s = SmartSearch()
        old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        facts = [
            _fact("Permanent system fact", updated_at=old_date, is_permanent=True),
        ]
        results = s.rank(facts, "system")
        assert results[0].recency_score == 1.0

    def test_language_boost(self) -> None:
        s = SmartSearch()
        facts = [
            _fact("English fact about games", language="en"),
            _fact("Russian fact about games", language="ru"),
        ]
        results = s.rank(facts, "games", language="ru")
        assert results[0].fact.language == "ru"

    def test_category_boost(self) -> None:
        s = SmartSearch()
        facts = [
            _fact("Game saves", category="games"),
            _fact("Work saves", category="work"),
        ]
        results = s.rank(facts, "saves", category="games")
        assert results[0].fact.category == "games"

    def test_limit(self) -> None:
        s = SmartSearch()
        facts = [_fact(f"Fact {i}") for i in range(20)]
        results = s.rank(facts, "fact", limit=5)
        assert len(results) == 5

    def test_scores_in_0_1_range(self) -> None:
        s = SmartSearch()
        facts = [_fact("Test", access_count=100)]
        results = s.rank(facts, "test")
        for r in results:
            assert 0.0 <= r.score <= 1.0
