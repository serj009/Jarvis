"""EmbeddingIndex: FAISS-backed semantic search for memory facts (T4.1/T4.2).

Provides vector similarity search using sentence-transformers embeddings
and a FAISS flat index. Designed as a parallel search path alongside the
keyword-based SmartSearch — results from both are merged by SmartSearch
for hybrid ranking.

Model: paraphrase-multilingual-MiniLM-L12-v2 (~420 MB, 384-dim, 50+ langs)
Index: FAISS IndexFlatIP (inner product on L2-normalized vectors = cosine)

Fully local: the model is downloaded once from HuggingFace and cached.
After the first download, works completely offline (HF_HUB_OFFLINE=1).

Implements the Loadable protocol for LifecycleManager integration:
    - load()   → load model + FAISS index from disk
    - unload() → release model and index from memory

Usage:
    idx = EmbeddingIndex(index_path=Path("data/embeddings"))
    await idx.load()
    idx.add("abc123", "User bought a new car")
    results = idx.search("vehicles", top_k=5)  # → [("abc123", 0.87)]
    idx.save()
    await idx.unload()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.memory.embedding_cache import EmbeddingCache

log = logging.getLogger(__name__)

# Default model — multilingual, CPU-friendly, 384-dim embeddings.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Default index storage directory (relative to data root).
_DEFAULT_INDEX_DIR = "embeddings"


def _default_index_root() -> Path:
    """Default path for embedding index files."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis" / "memory" / _DEFAULT_INDEX_DIR
    return Path.home() / ".jarvis" / "memory" / _DEFAULT_INDEX_DIR


class EmbeddingIndex:
    """FAISS-backed semantic search index for memory facts.

    Implements the Loadable protocol (name, is_loaded, load, unload).

    The sentence-transformers model is loaded lazily: importing the heavy
    libraries and loading weights only happens on the first ``load()`` call.
    This keeps startup fast when embeddings are disabled or the model is
    not yet downloaded.
    """

    name: str = "embedding_index"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        index_path: Path | None = None,
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._index_path = index_path or _default_index_root()
        self._device = device
        self._embedding_cache = EmbeddingCache(max_size=2000)

        # Lazy-loaded heavy objects.
        self._model = None  # SentenceTransformer
        self._index = None  # faiss.IndexFlatIP
        self._id_map: dict[str, int] = {}   # fact_id → position in index
        self._pos_map: dict[int, str] = {}  # position → fact_id
        self._is_loaded = False

    @property
    def embedding_cache(self) -> EmbeddingCache:
        """Access the embedding cache for stats or manual control."""
        return self._embedding_cache

    # -- Loadable protocol --------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    async def load(self) -> None:
        """Load the sentence-transformers model and FAISS index from disk.

        Idempotent: no-op if already loaded. If the model is not
        downloaded yet, sentence-transformers will download it from
        HuggingFace Hub on the first call (requires internet). After
        that, ``local_files_only=True`` ensures offline operation.
        """
        if self._is_loaded:
            return

        import asyncio
        await asyncio.to_thread(self._sync_load)
        self._is_loaded = True
        log.info(
            "embedding index loaded: model=%s, vectors=%d, path=%s",
            self._model_name, self.count, self._index_path,
        )

    def _sync_load(self) -> None:
        """Synchronous loading of model + index (runs in thread)."""
        self._load_model()
        self._load_index()

    def _load_model(self) -> None:
        """Load the SentenceTransformer model (lazy import)."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            log.error(
                "sentence-transformers not installed; "
                "run: pip install sentence-transformers faiss-cpu"
            )
            raise

        # Try local-only first (offline mode); fall back to download.
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
                local_files_only=True,
            )
            log.info("embedding model loaded from cache (offline)")
        except Exception:
            # Model not cached yet — download it.
            log.info("embedding model not cached; downloading %s...", self._model_name)
            env_backup = os.environ.pop("HF_HUB_OFFLINE", None)
            try:
                self._model = SentenceTransformer(
                    self._model_name,
                    device=self._device,
                )
            finally:
                if env_backup is not None:
                    os.environ["HF_HUB_OFFLINE"] = env_backup
            log.info("embedding model downloaded and loaded")

    def _load_index(self) -> None:
        """Load FAISS index and ID map from disk, or create empty."""
        import faiss
        import numpy as np

        index_file = self._index_path / "faiss.index"
        id_map_file = self._index_path / "id_map.json"

        if index_file.is_file() and id_map_file.is_file():
            try:
                self._index = faiss.read_index(str(index_file))
                with open(id_map_file, "r", encoding="utf-8") as f:
                    self._id_map = json.load(f)
                self._pos_map = {v: k for k, v in self._id_map.items()}
                log.info(
                    "FAISS index loaded from disk: %d vectors", self._index.ntotal
                )
                return
            except Exception:
                log.warning("failed to load FAISS index from disk; creating new", exc_info=True)

        # Create a new empty index (384-dim, inner product for cosine sim).
        dim = self._get_dim()
        self._index = faiss.IndexFlatIP(dim)
        self._id_map = {}
        self._pos_map = {}

    def _get_dim(self) -> int:
        """Get embedding dimensionality from the loaded model."""
        if self._model is not None:
            dim = self._model.get_sentence_embedding_dimension()
            return dim if dim else 384
        return 384  # Default for MiniLM-L12-v2.

    async def unload(self) -> None:
        """Release model and index from memory."""
        if not self._is_loaded:
            return
        self._model = None
        self._index = None
        self._id_map = {}
        self._pos_map = {}
        self._is_loaded = False
        # Clear embedding cache on unload.
        self._embedding_cache.clear()
        log.info("embedding index unloaded")

    # -- Public API ---------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of vectors in the index."""
        if self._index is None:
            return 0
        return self._index.ntotal

    def add(self, fact_id: str, text: str) -> None:
        """Add a single fact's embedding to the index.

        If the fact_id already exists, it is updated (removed + re-added).
        """
        if self._model is None or self._index is None:
            return

        import numpy as np

        # Remove existing if updating.
        if fact_id in self._id_map:
            self.remove(fact_id)

        vec = self._encode(text)
        pos = self._index.ntotal
        self._index.add(vec)
        self._id_map[fact_id] = pos
        self._pos_map[pos] = fact_id

    def remove(self, fact_id: str) -> None:
        """Remove a fact from the index.

        FAISS IndexFlatIP does not support in-place removal. We mark the
        position as deleted (tombstone) and compact on save/rebuild.
        For small indices (<10k) this is acceptable; the next save()
        or rebuild() will produce a clean index.
        """
        if fact_id not in self._id_map:
            return

        pos = self._id_map.pop(fact_id)
        self._pos_map.pop(pos, None)
        # Tombstone: position still exists in FAISS but is unmapped.
        # search() skips unmapped positions.

    def search(self, query: str, *, top_k: int = 10) -> list[tuple[str, float]]:
        """Semantic search: return [(fact_id, score)] sorted by relevance.

        Scores are cosine similarities in [0, 1] (L2-normalized vectors
        with inner product).
        """
        if self._model is None or self._index is None or self._index.ntotal == 0:
            return []

        import numpy as np

        vec = self._encode(query)
        # Search more than top_k to account for tombstones.
        search_k = min(top_k * 2, self._index.ntotal)
        scores, indices = self._index.search(vec, search_k)

        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            fact_id = self._pos_map.get(int(idx))
            if fact_id is None:
                continue  # Tombstoned entry.
            # Clamp score to [0, 1].
            results.append((fact_id, float(max(0.0, min(1.0, score)))))
            if len(results) >= top_k:
                break

        return results

    def rebuild(self, facts: list[tuple[str, str]]) -> None:
        """Full re-index from scratch: [(fact_id, text), ...].

        Replaces the entire FAISS index. Use after bulk import or to
        compact after many remove() calls.
        """
        if self._model is None:
            log.warning("cannot rebuild: model not loaded")
            return

        import faiss
        import numpy as np

        dim = self._get_dim()
        self._index = faiss.IndexFlatIP(dim)
        self._id_map = {}
        self._pos_map = {}

        if not facts:
            return

        texts = [text for _, text in facts]
        ids = [fid for fid, _ in facts]

        # Batch encode for efficiency.
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )
        vecs = np.asarray(vecs, dtype=np.float32)
        self._index.add(vecs)

        for pos, fid in enumerate(ids):
            self._id_map[fid] = pos
            self._pos_map[pos] = fid

        log.info("embedding index rebuilt: %d vectors", self._index.ntotal)

    def save(self) -> None:
        """Save the FAISS index and ID map to disk.

        If there are tombstones (removed entries), the index is compacted
        first to avoid storing dead vectors.
        """
        if self._index is None:
            return

        import faiss
        import numpy as np

        self._index_path.mkdir(parents=True, exist_ok=True)
        index_file = self._index_path / "faiss.index"
        id_map_file = self._index_path / "id_map.json"

        # Compact: rebuild without tombstones if needed.
        if self._index.ntotal > len(self._id_map) and self._model is not None:
            self._compact()

        faiss.write_index(self._index, str(index_file))
        with open(id_map_file, "w", encoding="utf-8") as f:
            json.dump(self._id_map, f, ensure_ascii=False)

        log.info(
            "embedding index saved: %d vectors → %s",
            self._index.ntotal,
            self._index_path,
        )

    # -- Internal -----------------------------------------------------------

    def _encode(self, text: str):
        """Encode text to a normalized embedding vector (1, dim)."""
        import numpy as np

        # Check embedding cache first.
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return np.asarray([cached], dtype=np.float32)

        vec = self._raw_encode(text)
        # Store in cache (as list for serializability).
        self._embedding_cache.put(text, vec[0].tolist())
        return vec

    def _raw_encode(self, text: str):
        """Encode text without cache (for internal use and batch ops)."""
        import numpy as np

        vec = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vec, dtype=np.float32)

    def _compact(self) -> None:
        """Rebuild the index keeping only live entries (no tombstones)."""
        import faiss
        import numpy as np

        if self._index is None or self._index.ntotal == 0:
            return

        # Reconstruct all live vectors.
        live_ids: list[str] = []
        live_vecs: list = []
        for pos in sorted(self._pos_map.keys()):
            fid = self._pos_map[pos]
            if fid in self._id_map:
                vec = self._index.reconstruct(pos)
                live_vecs.append(vec)
                live_ids.append(fid)

        dim = self._get_dim()
        self._index = faiss.IndexFlatIP(dim)
        self._id_map = {}
        self._pos_map = {}

        if live_vecs:
            vecs = np.stack(live_vecs).astype(np.float32)
            self._index.add(vecs)
            for new_pos, fid in enumerate(live_ids):
                self._id_map[fid] = new_pos
                self._pos_map[new_pos] = fid

        log.debug("compacted index: %d live vectors", self._index.ntotal)
