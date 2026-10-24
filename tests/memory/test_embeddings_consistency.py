"""Task H: EmbeddingIndex writes the FAISS index + id_map consistently, and a
mismatched pair is rejected on load instead of being used silently.

faiss is an optional native dep; these tests importorskip it so a lightweight
venv skips them cleanly rather than failing to collect. They do NOT need the
sentence-transformers model — the index is populated with raw vectors.
"""

from __future__ import annotations

import json
import logging

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from jarvis.memory.embeddings import EmbeddingIndex  # noqa: E402


def _make_index_with(idx: EmbeddingIndex, n: int, dim: int = 384) -> None:
    """Populate idx with *n* unit vectors and a matching id_map (no model)."""
    index = faiss.IndexFlatIP(dim)
    vecs = np.random.rand(n, dim).astype("float32")
    faiss.normalize_L2(vecs)
    index.add(vecs)
    idx._index = index
    idx._id_map = {f"fact{i}": i for i in range(n)}
    idx._pos_map = {i: f"fact{i}" for i in range(n)}


def test_save_writes_consistent_pair(tmp_path):
    idx = EmbeddingIndex(index_path=tmp_path)
    _make_index_with(idx, 5)
    idx.save()

    index_file = tmp_path / "faiss.index"
    id_map_file = tmp_path / "id_map.json"
    assert index_file.is_file() and id_map_file.is_file()
    # No temp debris from the two-phase write.
    assert [p for p in tmp_path.iterdir() if ".tmp" in p.name] == []

    loaded = faiss.read_index(str(index_file))
    id_map = json.loads(id_map_file.read_text(encoding="utf-8"))
    assert loaded.ntotal == len(id_map) == 5


def test_load_rejects_mismatched_pair(tmp_path, caplog):
    idx = EmbeddingIndex(index_path=tmp_path)
    _make_index_with(idx, 5)
    idx.save()

    # Corrupt consistency: shrink id_map so it no longer matches index.ntotal.
    id_map_file = tmp_path / "id_map.json"
    id_map_file.write_text(json.dumps({"fact0": 0, "fact1": 1}), encoding="utf-8")

    idx2 = EmbeddingIndex(index_path=tmp_path)
    with caplog.at_level(logging.WARNING, logger="jarvis.memory.embeddings"):
        idx2._load_index()

    # Mismatch detected, warned, and NOT used — a fresh empty index instead.
    assert any("mismatch" in r.getMessage().lower() for r in caplog.records)
    assert idx2._index is not None
    assert idx2._index.ntotal == 0
    assert idx2._id_map == {}


def test_load_accepts_matching_pair(tmp_path):
    idx = EmbeddingIndex(index_path=tmp_path)
    _make_index_with(idx, 3)
    idx.save()

    idx2 = EmbeddingIndex(index_path=tmp_path)
    idx2._load_index()
    assert idx2._index.ntotal == 3
    assert len(idx2._id_map) == 3
