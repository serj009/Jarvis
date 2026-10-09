"""SmartSearch: ranked search with time decay, frequency boost, language boost.

Goes beyond simple FTS5/LIKE matching by scoring results with multiple
signals:

1. **Text relevance** — FTS5 rank or keyword match count
2. **Frequency boost** — facts accessed more often score higher (popular = important)
3. **Time decay** — newer facts score higher (unless permanent)
4. **Language boost** — facts in the user's current language score higher
5. **Category boost** — if search has a category hint, matching category scores higher
6. **Semantic similarity** — FAISS cosine similarity (when EmbeddingIndex is available)

Result is a unified 0.0-1.0 score per fact, sorted descending.

When an EmbeddingIndex is provided, semantic similarity becomes the strongest
signal (~0.35 weight) and keyword weights are rebalanced. Without an
EmbeddingIndex, the original 5-signal keyword ranking works unchanged.

Usage:
    searcher = SmartSearch()
    results = searcher.rank(facts, query="stalker saves", language="ru")

    # With embeddings:
    searcher = SmartSearch(embedding_index=idx)
    results = searcher.rank(facts, query="vehicles", language="en")
    # → finds facts about "cars", "automobiles", etc.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from jarvis.memory.store import Fact

if TYPE_CHECKING:
    from jarvis.memory.embeddings import EmbeddingIndex
    from jarvis.memory.relations import RelationStore


# ---------------------------------------------------------------------------
# Scoring weights — keyword-only mode (no embeddings)
# ---------------------------------------------------------------------------

WEIGHT_TEXT_RELEVANCE = 0.40
WEIGHT_FREQUENCY = 0.20
WEIGHT_RECENCY = 0.20
WEIGHT_LANGUAGE = 0.10
WEIGHT_CATEGORY = 0.10

# ---------------------------------------------------------------------------
# Scoring weights — hybrid mode (with embeddings)
# ---------------------------------------------------------------------------

WEIGHT_SEMANTIC = 0.35
WEIGHT_TEXT_HYBRID = 0.20
WEIGHT_FREQUENCY_HYBRID = 0.15
WEIGHT_RECENCY_HYBRID = 0.15
WEIGHT_LANGUAGE_HYBRID = 0.08
WEIGHT_CATEGORY_HYBRID = 0.07

# Time decay half-life in days: after this many days, recency score is 0.5
RECENCY_HALF_LIFE_DAYS = 90.0

# Max access_count for normalization (facts above this get 1.0 frequency score)
FREQUENCY_CAP = 50


# ---------------------------------------------------------------------------
# Scored result
# ---------------------------------------------------------------------------

@dataclass
class ScoredFact:
    """A fact with a computed relevance score."""
    fact: Fact
    score: float
    text_score: float = 0.0
    frequency_score: float = 0.0
    recency_score: float = 0.0
    language_score: float = 0.0
    category_score: float = 0.0
    semantic_score: float = 0.0


# ---------------------------------------------------------------------------
# SmartSearch
# ---------------------------------------------------------------------------

class SmartSearch:
    """Ranked search across facts with multi-signal scoring."""

    def __init__(
        self,
        *,
        relation_store: RelationStore | None = None,
        embedding_index: EmbeddingIndex | None = None,
        weight_text: float = WEIGHT_TEXT_RELEVANCE,
        weight_frequency: float = WEIGHT_FREQUENCY,
        weight_recency: float = WEIGHT_RECENCY,
        weight_language: float = WEIGHT_LANGUAGE,
        weight_category: float = WEIGHT_CATEGORY,
        recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    ) -> None:
        self._relation_store = relation_store
        self._embedding_index = embedding_index

        # Store keyword-only weights (used when no embeddings).
        self._w_text_kw = weight_text
        self._w_freq_kw = weight_frequency
        self._w_recency_kw = weight_recency
        self._w_lang_kw = weight_language
        self._w_cat_kw = weight_category

        self._half_life = recency_half_life_days

    def set_relation_store(self, store: RelationStore | None) -> None:
        """Attach or detach a RelationStore for graph-enriched search."""
        self._relation_store = store

    def enrich_with_relations(self, fact_ids: set[str]) -> set[str]:
        """Expand a set of fact IDs with related facts from the graph.

        Traverses the relation graph (depth=1) for each input fact ID
        and returns the union of all discovered fact IDs.

        Returns the original set if RelationStore is not available.
        """
        if self._relation_store is None:
            return fact_ids

        enriched = set(fact_ids)
        try:
            for fid in list(fact_ids):
                related = self._relation_store.get_related(fid, depth=1)
                for rel in related:
                    from_id = rel.get("fact_id_from", "")
                    to_id = rel.get("fact_id_to", "")
                    if from_id:
                        enriched.add(from_id)
                    if to_id:
                        enriched.add(to_id)
        except Exception:
            pass  # Graceful degradation.
        return enriched

    @property
    def has_embeddings(self) -> bool:
        """Whether semantic search is available."""
        return (
            self._embedding_index is not None
            and self._embedding_index.is_loaded
            and self._embedding_index.count > 0
        )

    def rank(
        self,
        facts: list[Fact],
        query: str,
        *,
        language: str = "en",
        category: str | None = None,
        limit: int = 10,
    ) -> list[ScoredFact]:
        """Score and rank facts by multi-signal relevance."""
        if not facts:
            return []

        now = datetime.now(UTC)
        query_lower = query.lower()
        query_words = set(re.findall(r"\w+", query_lower))

        # Get semantic scores if embeddings are available.
        semantic_scores: dict[str, float] = {}
        use_semantic = self.has_embeddings
        if use_semantic:
            try:
                sem_results = self._embedding_index.search(
                    query, top_k=max(limit * 3, 50)
                )
                semantic_scores = {fid: score for fid, score in sem_results}
            except Exception:
                use_semantic = False

        # Select weight profile.
        if use_semantic:
            w_text = WEIGHT_TEXT_HYBRID
            w_freq = WEIGHT_FREQUENCY_HYBRID
            w_recency = WEIGHT_RECENCY_HYBRID
            w_lang = WEIGHT_LANGUAGE_HYBRID
            w_cat = WEIGHT_CATEGORY_HYBRID
            w_sem = WEIGHT_SEMANTIC
        else:
            w_text = self._w_text_kw
            w_freq = self._w_freq_kw
            w_recency = self._w_recency_kw
            w_lang = self._w_lang_kw
            w_cat = self._w_cat_kw
            w_sem = 0.0

        scored: list[ScoredFact] = []
        for fact in facts:
            text_score = self._text_relevance(fact.text, query_lower, query_words)
            freq_score = self._frequency_score(fact.access_count)
            recency = self._recency_score(fact, now)
            lang_score = 1.0 if fact.language == language else 0.3
            cat_score = 1.0 if (category and fact.category == category) else 0.5
            sem_score = semantic_scores.get(fact.id, 0.0) if use_semantic else 0.0

            total = (
                w_text * text_score
                + w_freq * freq_score
                + w_recency * recency
                + w_lang * lang_score
                + w_cat * cat_score
                + w_sem * sem_score
            )

            scored.append(ScoredFact(
                fact=fact,
                score=min(1.0, max(0.0, total)),
                text_score=text_score,
                frequency_score=freq_score,
                recency_score=recency,
                language_score=lang_score,
                category_score=cat_score,
                semantic_score=sem_score,
            ))

        scored.sort(key=lambda s: -s.score)
        return scored[:limit]

    # -- scoring components -------------------------------------------------

    @staticmethod
    def _text_relevance(text: str, query_lower: str, query_words: set[str]) -> float:
        """Score 0-1 based on keyword overlap."""
        if not query_words:
            return 0.5  # No query = neutral

        text_lower = text.lower()
        text_words = set(re.findall(r"\w+", text_lower))

        # Exact substring match bonus
        exact_match = 1.0 if query_lower in text_lower else 0.0

        # Word overlap ratio
        if not query_words:
            overlap = 0.0
        else:
            common = query_words & text_words
            overlap = len(common) / len(query_words)

        return min(1.0, exact_match * 0.5 + overlap * 0.5)

    @staticmethod
    def _frequency_score(access_count: int) -> float:
        """Score 0-1 based on access frequency."""
        if access_count <= 0:
            return 0.0
        return min(1.0, access_count / FREQUENCY_CAP)

    def _recency_score(self, fact: Fact, now: datetime) -> float:
        """Score 0-1 based on how recent the fact is. Permanent = always 1.0."""
        if fact.is_permanent:
            return 1.0

        try:
            updated = datetime.fromisoformat(fact.updated_at)
            # Make timezone-aware if not already
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return 0.5  # Can't parse date = neutral

        age_days = (now - updated).total_seconds() / 86400.0
        if age_days < 0:
            return 1.0

        # Exponential decay with half-life
        decay = math.exp(-0.693 * age_days / self._half_life)
        return max(0.0, min(1.0, decay))
