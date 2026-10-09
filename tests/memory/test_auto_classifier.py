"""Tests for jarvis.memory.auto_classifier (AutoClassifier).

Tests LLM-based entity classification with mocked Ollama responses,
LRU cache behavior, and graceful degradation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from jarvis.memory.auto_classifier import (
    AutoClassifier,
    ClassificationResult,
    ENTITY_TYPE_TO_SHARD,
)


# ---------------------------------------------------------------------------
# ClassificationResult tests
# ---------------------------------------------------------------------------

class TestClassificationResult:
    def test_frozen_dataclass(self) -> None:
        result = ClassificationResult(entity="Stalker 2", entity_type="game", shard="games")
        assert result.entity == "Stalker 2"
        assert result.entity_type == "game"
        assert result.shard == "games"
        # Frozen — assignment should raise.
        with pytest.raises(AttributeError):
            result.entity = "other"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        result = ClassificationResult(
            entity="Wukong", entity_type="game", full_name="Black Myth: Wukong",
            shard="games", confidence=0.85, source="llm", extra_info="Action RPG",
        )
        d = result.to_dict()
        assert d["entity"] == "Wukong"
        assert d["type"] == "game"
        assert d["full_name"] == "Black Myth: Wukong"
        assert d["shard"] == "games"
        assert d["confidence"] == 0.85

    def test_from_dict(self) -> None:
        d = {"entity": "Pizza", "type": "food", "shard": "cooking", "confidence": 0.9}
        result = ClassificationResult.from_dict(d)
        assert result.entity == "Pizza"
        assert result.entity_type == "food"
        assert result.shard == "cooking"

    def test_from_dict_defaults(self) -> None:
        result = ClassificationResult.from_dict({})
        assert result.entity == ""
        assert result.entity_type == "unknown"
        assert result.shard == "general"


# ---------------------------------------------------------------------------
# Entity type mapping tests
# ---------------------------------------------------------------------------

class TestEntityTypeMapping:
    def test_game_types(self) -> None:
        assert ENTITY_TYPE_TO_SHARD["game"] == "games"
        assert ENTITY_TYPE_TO_SHARD["video_game"] == "games"

    def test_media_types(self) -> None:
        assert ENTITY_TYPE_TO_SHARD["movie"] == "tv_media"
        assert ENTITY_TYPE_TO_SHARD["anime"] == "tv_media"

    def test_tech_types(self) -> None:
        assert ENTITY_TYPE_TO_SHARD["technology"] == "tech"
        assert ENTITY_TYPE_TO_SHARD["software"] == "tech"

    def test_food_types(self) -> None:
        assert ENTITY_TYPE_TO_SHARD["food"] == "cooking"
        assert ENTITY_TYPE_TO_SHARD["recipe"] == "cooking"


# ---------------------------------------------------------------------------
# AutoClassifier tests (mocked LLM)
# ---------------------------------------------------------------------------

class TestAutoClassifier:
    @pytest.fixture()
    def classifier(self, tmp_path) -> AutoClassifier:
        """Create a classifier with a temp cache path."""
        return AutoClassifier(
            ollama_endpoint="http://localhost:11434",
            model="qwen2.5:7b-instruct",
            cache_path=tmp_path / "test_cache.json",
            max_cache=100,
        )

    @pytest.mark.asyncio()
    async def test_classify_llm_success(self, classifier) -> None:
        """Successful LLM classification returns correct result."""
        llm_response = json.dumps({
            "type": "game",
            "full_name": "Black Myth: Wukong",
            "brief": "Action RPG game",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": llm_response}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await classifier.classify("Wukong", context="collect brushes")

        assert result.entity_type == "game"
        assert result.shard == "games"
        assert result.confidence == 0.85
        assert result.source == "llm"
        assert result.full_name == "Black Myth: Wukong"

    @pytest.mark.asyncio()
    async def test_classify_cache_hit(self, classifier) -> None:
        """Second call for same entity returns from cache."""
        llm_response = json.dumps({
            "type": "game",
            "full_name": "Stalker 2",
            "brief": "FPS game",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": llm_response}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # First call → LLM
            result1 = await classifier.classify("Stalker 2")
            assert result1.source == "llm"

            # Second call → cache
            result2 = await classifier.classify("Stalker 2")
            assert result2.source == "cache"
            assert result2.entity_type == "game"

            # LLM should have been called only once.
            assert mock_client.post.call_count == 1

    @pytest.mark.asyncio()
    async def test_classify_case_insensitive_cache(self, classifier) -> None:
        """Cache keys are case-insensitive."""
        llm_response = json.dumps({"type": "game", "full_name": "Diablo"})

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": llm_response}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await classifier.classify("Diablo")
            result = await classifier.classify("DIABLO")
            assert result.source == "cache"

    @pytest.mark.asyncio()
    async def test_classify_llm_failure_returns_unknown(self, classifier) -> None:
        """When LLM fails, returns unknown/general."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await classifier.classify("SomeUnknownThing")

        assert result.shard == "general"
        assert result.source == "none"

    @pytest.mark.asyncio()
    async def test_classify_no_httpx(self, classifier) -> None:
        """When httpx is not available, returns unknown."""
        with patch.dict("sys.modules", {"httpx": None}):
            # Force ImportError on next import attempt
            with patch(
                "jarvis.memory.auto_classifier.AutoClassifier._classify_with_llm",
                return_value=None,
            ):
                result = await classifier.classify("SomeThing")
        assert result.shard == "general"

    def test_cache_persistence(self, tmp_path) -> None:
        """Cache persists to JSON and reloads."""
        cache_path = tmp_path / "cache.json"

        # Create and populate cache manually.
        data = {
            "stalker 2": {
                "entity": "Stalker 2",
                "type": "game",
                "full_name": "S.T.A.L.K.E.R. 2",
                "shard": "games",
                "confidence": 0.85,
                "source": "llm",
            }
        }
        with open(cache_path, "w") as f:
            json.dump(data, f)

        # Load and verify.
        classifier = AutoClassifier(cache_path=cache_path)
        assert classifier.get_stats()["cached"] == 1

    def test_lru_eviction(self, tmp_path) -> None:
        """LRU cache evicts oldest when full."""
        classifier = AutoClassifier(
            cache_path=tmp_path / "cache.json",
            max_cache=3,
        )
        # Manually populate cache.
        for i in range(5):
            classifier._store(
                f"entity_{i}",
                ClassificationResult(entity=f"e{i}", entity_type="game", shard="games"),
            )
        # Only last 3 should remain.
        assert len(classifier._cache) == 3
        assert "entity_0" not in classifier._cache
        assert "entity_1" not in classifier._cache
        assert "entity_4" in classifier._cache

    def test_get_stats(self, tmp_path) -> None:
        classifier = AutoClassifier(cache_path=tmp_path / "c.json")
        stats = classifier.get_stats()
        assert stats["cached"] == 0
