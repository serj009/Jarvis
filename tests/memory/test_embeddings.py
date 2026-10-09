"""Tests for jarvis.memory.embeddings and semantic search integration.

These tests exercise EmbeddingIndex lifecycle (add, search, remove, rebuild,
save/load), semantic search quality (synonym matching, multilingual), graceful
degradation (when model/FAISS unavailable), and SmartSearch hybrid scoring.

Tests that require the actual sentence-transformers model (~420 MB) are
marked with ``@pytest.mark.slow`` so the default ``pytest`` run stays fast.
Run with ``pytest -m slow`` to include them.
"""

from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.memory.smart_search import SmartSearch, ScoredFact
from jarvis.memory.store import Fact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fact(
    text: str,
    *,
    fact_id: str = "test",
    category: str = "general",
    language: str = "en",
    access_count: int = 0,
    updated_at: str | None = None,
    is_permanent: bool = False,
) -> Fact:
    now = datetime.now(UTC).isoformat()
    return Fact(
        id=fact_id,
        text=text,
        category=category,
        language=language,
        access_count=access_count,
        updated_at=updated_at or now,
        created_at=now,
        is_permanent=is_permanent,
    )


# ---------------------------------------------------------------------------
# EmbeddingIndex unit tests (mock-based, no real model)
# ---------------------------------------------------------------------------

class TestEmbeddingIndexMocked:
    """Tests using a mocked sentence-transformers model."""

    def _make_index(self, tmp_path: Path):
        """Create an EmbeddingIndex with mocked model loading."""
        from jarvis.memory.embeddings import EmbeddingIndex
        return EmbeddingIndex(index_path=tmp_path / "embeddings")

    @pytest.fixture
    def mock_st(self):
        """Mock sentence_transformers and faiss modules."""
        import numpy as np

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 4
        # encode returns normalized vectors
        def _encode(texts, **kwargs):
            vecs = []
            for t in texts:
                # Simple deterministic "embedding" based on hash
                h = hash(t) % 10000
                v = np.array([h % 10, (h // 10) % 10, (h // 100) % 10, (h // 1000) % 10], dtype=np.float32)
                norm = np.linalg.norm(v)
                if norm > 0:
                    v = v / norm
                vecs.append(v)
            return np.array(vecs, dtype=np.float32)

        mock_model.encode = _encode

        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.return_value = mock_model

        return mock_st_module, mock_model

    def test_add_and_search(self, tmp_path, mock_st):
        """Add facts and search returns results."""
        mock_st_module, _ = mock_st

        with patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
            from jarvis.memory.embeddings import EmbeddingIndex
            idx = EmbeddingIndex(index_path=tmp_path / "emb")
            # Manually set model and index
            import faiss, numpy as np
            idx._model = mock_st_module.SentenceTransformer()
            idx._index = faiss.IndexFlatIP(4)
            idx._id_map = {}
            idx._pos_map = {}
            idx._is_loaded = True

            idx.add("fact1", "I bought a new car")
            idx.add("fact2", "The weather is sunny")
            idx.add("fact3", "My cat is sleeping")

            assert idx.count == 3

            results = idx.search("automobile", top_k=2)
            assert len(results) > 0
            # All results should have (fact_id, score) tuples
            for fid, score in results:
                assert isinstance(fid, str)
                assert 0.0 <= score <= 1.0

    def test_remove(self, tmp_path, mock_st):
        """Remove marks a fact as deleted (tombstone)."""
        mock_st_module, _ = mock_st

        with patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
            from jarvis.memory.embeddings import EmbeddingIndex
            idx = EmbeddingIndex(index_path=tmp_path / "emb")
            import faiss
            idx._model = mock_st_module.SentenceTransformer()
            idx._index = faiss.IndexFlatIP(4)
            idx._id_map = {}
            idx._pos_map = {}
            idx._is_loaded = True

            idx.add("fact1", "Test fact one")
            idx.add("fact2", "Test fact two")
            assert idx.count == 2

            idx.remove("fact1")
            # FAISS still has 2 vectors, but fact1 is tombstoned
            assert "fact1" not in idx._id_map

            results = idx.search("test", top_k=10)
            fact_ids = [fid for fid, _ in results]
            assert "fact1" not in fact_ids

    def test_rebuild(self, tmp_path, mock_st):
        """Rebuild replaces the entire index."""
        mock_st_module, _ = mock_st

        with patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
            from jarvis.memory.embeddings import EmbeddingIndex
            idx = EmbeddingIndex(index_path=tmp_path / "emb")
            import faiss
            idx._model = mock_st_module.SentenceTransformer()
            idx._index = faiss.IndexFlatIP(4)
            idx._id_map = {}
            idx._pos_map = {}
            idx._is_loaded = True

            idx.add("old1", "Old fact")
            assert idx.count == 1

            idx.rebuild([
                ("new1", "New fact one"),
                ("new2", "New fact two"),
                ("new3", "New fact three"),
            ])
            assert idx.count == 3
            assert "old1" not in idx._id_map
            assert "new1" in idx._id_map

    def test_save_and_load(self, tmp_path, mock_st):
        """Save index to disk, then load it back."""
        mock_st_module, _ = mock_st

        with patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
            from jarvis.memory.embeddings import EmbeddingIndex
            import faiss
            idx = EmbeddingIndex(index_path=tmp_path / "emb")
            idx._model = mock_st_module.SentenceTransformer()
            idx._index = faiss.IndexFlatIP(4)
            idx._id_map = {}
            idx._pos_map = {}
            idx._is_loaded = True

            idx.add("fact1", "Hello world")
            idx.add("fact2", "Goodbye world")
            idx.save()

            # Verify files exist
            assert (tmp_path / "emb" / "faiss.index").is_file()
            assert (tmp_path / "emb" / "id_map.json").is_file()

            # Load into a new instance
            idx2 = EmbeddingIndex(index_path=tmp_path / "emb")
            idx2._model = mock_st_module.SentenceTransformer()
            idx2._load_index()

            assert idx2.count == 2
            assert "fact1" in idx2._id_map
            assert "fact2" in idx2._id_map

    def test_count_property(self, tmp_path, mock_st):
        """Count returns 0 when index is None."""
        from jarvis.memory.embeddings import EmbeddingIndex
        idx = EmbeddingIndex(index_path=tmp_path / "emb")
        assert idx.count == 0
        assert idx.is_loaded is False

    def test_search_empty_returns_empty(self, tmp_path, mock_st):
        """Search on empty/None index returns empty list."""
        from jarvis.memory.embeddings import EmbeddingIndex
        idx = EmbeddingIndex(index_path=tmp_path / "emb")
        assert idx.search("test") == []

    def test_add_update_existing(self, tmp_path, mock_st):
        """Adding a fact_id that already exists replaces it."""
        mock_st_module, _ = mock_st

        with patch.dict("sys.modules", {"sentence_transformers": mock_st_module}):
            from jarvis.memory.embeddings import EmbeddingIndex
            import faiss
            idx = EmbeddingIndex(index_path=tmp_path / "emb")
            idx._model = mock_st_module.SentenceTransformer()
            idx._index = faiss.IndexFlatIP(4)
            idx._id_map = {}
            idx._pos_map = {}
            idx._is_loaded = True

            idx.add("fact1", "Version one")
            old_pos = idx._id_map["fact1"]

            idx.add("fact1", "Version two")
            new_pos = idx._id_map["fact1"]

            # Position changed (old was tombstoned, new was appended)
            assert new_pos != old_pos
            # Only one mapping for fact1
            assert sum(1 for v in idx._pos_map.values() if v == "fact1") == 1


# ---------------------------------------------------------------------------
# SmartSearch integration tests (with mock embeddings)
# ---------------------------------------------------------------------------

class TestSmartSearchWithEmbeddings:
    """Test SmartSearch hybrid scoring when EmbeddingIndex is available."""

    def _mock_embedding_index(self, scores: dict[str, float]):
        """Create a mock EmbeddingIndex that returns predefined scores."""
        idx = MagicMock()
        idx.is_loaded = True
        idx.count = 10
        idx.search.return_value = [(fid, s) for fid, s in scores.items()]
        return idx

    def test_semantic_signal_included(self):
        """When embeddings are available, semantic_score is populated."""
        scores = {"f1": 0.95, "f2": 0.3}
        idx = self._mock_embedding_index(scores)

        search = SmartSearch(embedding_index=idx)
        facts = [
            _fact("I bought a new car", fact_id="f1"),
            _fact("The weather is nice", fact_id="f2"),
        ]
        results = search.rank(facts, "automobile")
        assert results[0].semantic_score > 0
        assert results[0].fact.id == "f1"  # Higher semantic score wins

    def test_keyword_only_when_no_embeddings(self):
        """Without embeddings, semantic_score is always 0."""
        search = SmartSearch()  # No embedding_index
        facts = [_fact("Hello world", fact_id="f1")]
        results = search.rank(facts, "hello")
        assert results[0].semantic_score == 0.0

    def test_has_embeddings_false_when_not_loaded(self):
        """has_embeddings is False when index exists but isn't loaded."""
        idx = MagicMock()
        idx.is_loaded = False
        idx.count = 0
        search = SmartSearch(embedding_index=idx)
        assert search.has_embeddings is False

    def test_has_embeddings_false_when_empty(self):
        """has_embeddings is False when index is loaded but empty."""
        idx = MagicMock()
        idx.is_loaded = True
        idx.count = 0
        search = SmartSearch(embedding_index=idx)
        assert search.has_embeddings is False

    def test_graceful_degradation_on_search_error(self):
        """If embedding search throws, fall back to keyword-only."""
        idx = MagicMock()
        idx.is_loaded = True
        idx.count = 10
        idx.search.side_effect = RuntimeError("FAISS error")

        search = SmartSearch(embedding_index=idx)
        facts = [_fact("Test fact", fact_id="f1")]
        # Should not raise — gracefully falls back
        results = search.rank(facts, "test")
        assert len(results) == 1
        assert results[0].semantic_score == 0.0

    def test_backward_compatible_existing_tests(self):
        """Original 5-signal tests still pass with no embeddings."""
        s = SmartSearch()
        facts = [
            _fact("The weather is sunny today"),
            _fact("I like pizza"),
        ]
        results = s.rank(facts, "weather")
        assert results[0].fact.text == "The weather is sunny today"
        assert results[0].score > results[1].score

    def test_scored_fact_has_semantic_field(self):
        """ScoredFact dataclass includes semantic_score field."""
        sf = ScoredFact(
            fact=_fact("test"),
            score=0.5,
            semantic_score=0.8,
        )
        assert sf.semantic_score == 0.8


# ---------------------------------------------------------------------------
# Integration tests requiring real model (slow)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestEmbeddingIndexReal:
    """Tests using the real sentence-transformers model.

    Requires: pip install sentence-transformers faiss-cpu
    Run with: pytest -m slow tests/memory/test_embeddings.py
    """

    @pytest.fixture
    def real_index(self, tmp_path):
        """Create a real EmbeddingIndex with the actual model."""
        try:
            from jarvis.memory.embeddings import EmbeddingIndex
            idx = EmbeddingIndex(index_path=tmp_path / "embeddings")
            # Synchronous load for test
            idx._sync_load()
            idx._is_loaded = True
            return idx
        except ImportError:
            pytest.skip("sentence-transformers or faiss-cpu not installed")

    def test_semantic_synonym_search(self, real_index):
        """'машины' (cars in Russian) finds 'купил новый автомобиль'."""
        real_index.add("f1", "Купил новый автомобиль Toyota")
        real_index.add("f2", "Погода сегодня солнечная")
        real_index.add("f3", "Люблю пиццу с грибами")

        results = real_index.search("машины", top_k=3)
        assert len(results) > 0
        # The car fact should rank first
        assert results[0][0] == "f1"
        assert results[0][1] > 0.5  # High similarity

    def test_multilingual_cross_search(self, real_index):
        """'cars' (English) finds 'автомобиль' (Russian)."""
        real_index.add("f1", "Купил новый автомобиль")
        real_index.add("f2", "Сегодня хорошая погода")

        results = real_index.search("cars", top_k=3)
        assert len(results) > 0
        assert results[0][0] == "f1"

    def test_english_synonym_search(self, real_index):
        """'vehicles' finds 'bought a new car'."""
        real_index.add("f1", "I bought a new car last week")
        real_index.add("f2", "The meeting is at 3 PM")

        results = real_index.search("vehicles", top_k=3)
        assert len(results) > 0
        assert results[0][0] == "f1"

    def test_ukrainian_search(self, real_index):
        """Ukrainian query finds Ukrainian fact."""
        real_index.add("f1", "Купив новий автомобіль минулого тижня")
        real_index.add("f2", "Сьогодні гарна погода")

        results = real_index.search("машини", top_k=3)
        assert len(results) > 0
        assert results[0][0] == "f1"

    def test_rebuild_and_search(self, real_index):
        """Full rebuild produces a searchable index."""
        real_index.rebuild([
            ("f1", "My favorite game is Stalker 2"),
            ("f2", "I work at a tech company"),
            ("f3", "Pizza is the best food"),
        ])
        assert real_index.count == 3

        results = real_index.search("video games", top_k=2)
        assert results[0][0] == "f1"

    def test_save_load_round_trip(self, real_index, tmp_path):
        """Save to disk and load back preserves search quality."""
        real_index.add("f1", "Bought a Tesla Model 3")
        real_index.add("f2", "Learned Python programming")
        real_index.save()

        # Load into new instance
        from jarvis.memory.embeddings import EmbeddingIndex
        idx2 = EmbeddingIndex(index_path=tmp_path / "embeddings")
        idx2._sync_load()
        idx2._is_loaded = True

        assert idx2.count == 2
        results = idx2.search("electric car", top_k=2)
        assert results[0][0] == "f1"

    def test_embedding_dimension(self, real_index):
        """Model produces 384-dimensional embeddings."""
        assert real_index._get_dim() == 384
