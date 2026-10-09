"""MemoryManager: unified facade for all memory subsystems.

Single entry point that coordinates:
    - MemoryStore (SQLite + FTS5 facts)
    - ConversationMemory (quarterly rotation)
    - ShardManager + DynamicShardManager (per-category sharding)
    - KnowledgeStore (read-only YAML knowledge)
    - SmartSearch (ranked multi-signal search)
    - EmbeddingIndex (FAISS semantic search — optional)
    - AutoTagger (auto-tagging on insert)
    - Caches (ResponseCache, IntentCache, ShortTermMemory)
    - AutoCleanup (periodic dedup + archival)
    - PredictivePreload (context-aware preloading)

Usage:
    mm = MemoryManager(data_root=Path(".../memory"))
    await mm.open()

    # Remember a fact
    fact_id = await mm.remember("Stalker 2 save at level 5")

    # Search with ranking (keyword + semantic if available)
    results = await mm.search("stalker saves")

    # Log conversation
    mm.log_turn("What's my save?", "Level 5 in Stalker 2")

    # Check cache before LLM
    cached = mm.check_response_cache("what's my stalker save")
    if cached:
        return cached  # Skip LLM
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.memory.auto_cleanup import AutoCleanup
from jarvis.memory.auto_classifier import AutoClassifier, ClassificationResult
from jarvis.memory.cache import IntentCache, ResponseCache, ShortTermMemory
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.embedding_cache import EmbeddingCache
from jarvis.memory.knowledge_store import KnowledgeStore
from jarvis.memory.relations import RelationStore
from jarvis.memory.smart_search import SmartSearch
from jarvis.memory.store import Fact, MemoryStore

if TYPE_CHECKING:
    from jarvis.memory.embeddings import EmbeddingIndex

log = logging.getLogger(__name__)


class MemoryManager:
    """Unified facade for all memory subsystems."""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        knowledge_dir: Path | None = None,
        embedding_index: EmbeddingIndex | None = None,
        ollama_endpoint: str = "http://localhost:11434",
        ollama_model: str = "qwen2.5:7b-instruct",
    ) -> None:
        self._data_root = data_root

        # Core stores
        self.store = MemoryStore(db_path=data_root / "memory.db" if data_root else None)
        self.conversations = ConversationMemory(
            db_dir=data_root / "conversations" if data_root else None
        )
        self.knowledge = KnowledgeStore(knowledge_dir=knowledge_dir)

        # AutoClassifier (LLM-based entity classification).
        cache_path = data_root / "entity_classifications.json" if data_root else None
        self._auto_classifier = AutoClassifier(
            ollama_endpoint=ollama_endpoint,
            model=ollama_model,
            cache_path=cache_path,
        )
        # Wire classifier into the tagger so callers can check has_classifier.
        self.store._tagger.set_classifier(self._auto_classifier)

        # RelationStore (fact graph).
        db_path = data_root / "memory.db" if data_root else None
        self._relation_store: RelationStore | None = None
        if db_path is not None:
            self._relation_store = RelationStore(db_path=db_path)

        # EmbeddingCache (LRU cache for embedding vectors).
        self._embedding_cache = EmbeddingCache(max_size=2000)

        # Embedding index (optional — semantic search).
        self._embedding_index = embedding_index
        if embedding_index is not None:
            self.store.set_embedding_index(embedding_index)

        # Search (with or without embeddings, with or without relations).
        self._search = SmartSearch(
            embedding_index=embedding_index,
            relation_store=self._relation_store,
        )

        # Caches
        self.response_cache = ResponseCache(max_size=100, ttl_seconds=300.0)
        self.intent_cache = IntentCache(
            path=data_root / "intent_cache.json" if data_root else None
        )
        self.short_term = ShortTermMemory(max_turns=10)

        # Cleanup
        self._cleanup = AutoCleanup(archive_days=365, archive_min_hits=0)

    async def open(self) -> None:
        """Open all stores. Non-fatal if any fails."""
        try:
            await self.store.open()
        except Exception:
            log.warning("memory store failed to open", exc_info=True)

        try:
            await self.conversations.open()
        except Exception:
            log.warning("conversation memory failed to open", exc_info=True)

        # Initialize relations schema.
        if self._relation_store is not None:
            try:
                self._relation_store.init_schema()
            except Exception:
                log.warning("relation store schema init failed", exc_info=True)

        # Load persistent caches
        self.intent_cache.load()

        # Load embedding index if provided.
        if self._embedding_index is not None:
            try:
                await self._embedding_index.load()
                # If index is empty but store has facts, rebuild.
                if (
                    self._embedding_index.is_loaded
                    and self._embedding_index.count == 0
                    and self.store.is_open
                ):
                    await self._rebuild_embeddings()
            except Exception:
                log.warning(
                    "embedding index failed to load (non-fatal); "
                    "semantic search will be unavailable",
                    exc_info=True,
                )

    async def _rebuild_embeddings(self) -> None:
        """Rebuild the FAISS index from all facts in the store."""
        if self._embedding_index is None or not self._embedding_index.is_loaded:
            return
        facts = await self.store.all_facts_for_embedding()
        if facts:
            import asyncio
            await asyncio.to_thread(self._embedding_index.rebuild, facts)
            self._embedding_index.save()
            log.info("embedding index rebuilt from %d facts", len(facts))

    async def close(self) -> None:
        """Close all stores and save caches."""
        # Save embedding index before closing.
        if self._embedding_index is not None and self._embedding_index.is_loaded:
            try:
                self._embedding_index.save()
                await self._embedding_index.unload()
            except Exception:
                log.warning("embedding index failed to save/unload", exc_info=True)

        try:
            await self.store.close()
        except Exception:
            pass
        try:
            await self.conversations.close()
        except Exception:
            pass
        self.intent_cache.save()

    # -- Remember --

    async def remember(
        self,
        text: str,
        *,
        category: str = "general",
        source: str = "user",
        tags: list[str] | None = None,
        is_permanent: bool = False,
    ) -> str:
        """Remember a fact. Auto-tagged. Returns fact ID."""
        fact_id = await self.store.add(
            text,
            category=category,
            source=source,
            tags=tags,
            is_permanent=is_permanent,
        )

        # Async LLM classification for entities that regex can't categorize.
        # If the auto-tagger left the category as "general" and we have a
        # classifier, try to classify key entities from the text.
        if category == "general" and self._auto_classifier is not None:
            try:
                result = await self._auto_classifier.classify(text)
                if result.confidence >= 0.7 and result.shard != "general":
                    # Update the fact's category to the classified one.
                    await self.store.update_category(fact_id, result.entity_type)
                    log.debug(
                        "auto-classified fact %s as %s (%s)",
                        fact_id, result.entity_type, result.shard,
                    )
            except Exception:
                pass  # Graceful degradation — classification is optional.

        return fact_id

    # -- Search --

    async def search(
        self,
        query: str,
        *,
        language: str = "en",
        category: str | None = None,
        limit: int = 5,
    ) -> list[Fact]:
        """Search with SmartSearch ranking (time decay, frequency, language, semantic)."""
        # 1. Check knowledge store first (instant, read-only)
        # (knowledge results are not Fact objects — separate path)

        # 2. Search memory store
        raw = await self.store.search(query, limit=20, category=category)

        # 2b. Enrich with related facts from the relation graph.
        #     If any of the keyword-matched facts have relations, pull
        #     the related facts into the candidate set.
        if self._relation_store is not None:
            try:
                raw_ids = {f.id for f in raw}
                enriched_ids = self._search.enrich_with_relations(raw_ids)
                new_ids = enriched_ids - raw_ids
                for fid in new_ids:
                    fact = await self.store.get(fid)
                    if fact is not None:
                        raw.append(fact)
            except Exception:
                pass  # Graceful degradation.

        # 3. If semantic search is available, also include facts found by
        #    embeddings that keyword search might have missed.
        if self._search.has_embeddings:
            try:
                sem_results = self._embedding_index.search(query, top_k=20)
                sem_ids = {fid for fid, _ in sem_results}
                raw_ids = {f.id for f in raw}
                # Fetch facts found only by semantic search.
                missing_ids = sem_ids - raw_ids
                for fid in missing_ids:
                    fact = await self.store.get(fid)
                    if fact is not None:
                        raw.append(fact)
            except Exception:
                pass  # Graceful degradation.

        scored = self._search.rank(raw, query, language=language, category=category, limit=limit)
        return [s.fact for s in scored]

    def search_knowledge(self, query: str, limit: int = 5) -> list[dict]:
        """Search read-only knowledge base (YAML)."""
        return self.knowledge.search(query, limit=limit)

    # -- Conversation --

    def log_turn(self, user_text: str, assistant_text: str) -> None:
        """Log a conversation turn to both ConversationMemory and ShortTermMemory."""
        self.short_term.add(user_text, assistant_text)
        # ConversationMemory.log_turn is async but we fire-and-forget here
        # The caller can await conversations.log_turn() directly if needed

    def get_context(self, n_turns: int = 5) -> str:
        """Get recent conversation context for LLM prompt injection."""
        return self.short_term.as_context_string(n_turns)

    # -- Caches --

    def check_response_cache(self, query: str) -> str | None:
        """Check if we have a cached LLM response. Returns None if miss."""
        return self.response_cache.get(query)

    def cache_response(self, query: str, response: str) -> None:
        """Cache an LLM response for future identical queries."""
        self.response_cache.put(query, response)

    def check_intent_cache(self, query: str) -> str | None:
        """Check if this query maps to a known tool. Returns tool name or None."""
        return self.intent_cache.get(query)

    def learn_intent(self, query: str, tool_name: str) -> None:
        """Learn that this query maps to this tool (persisted)."""
        self.intent_cache.learn(query, tool_name)
        self.intent_cache.save()

    # -- Relations --

    def add_relation(
        self, from_id: str, to_id: str, relation_type: str, strength: float = 1.0
    ) -> int | None:
        """Add a relation between two facts. Returns relation ID or None."""
        if self._relation_store is None:
            return None
        return self._relation_store.add_relation(from_id, to_id, relation_type, strength)

    def find_related(self, tag: str) -> list[str]:
        """Find all fact IDs related to a tag through the relation graph."""
        if self._relation_store is None:
            return []
        return self._relation_store.find_all_related_to_tag(tag)

    # -- Cleanup --

    async def run_cleanup(self) -> dict:
        """Run deduplication + archival on the main memory store."""
        if not self.store.is_open:
            return {"error": "store not open"}
        # AutoCleanup works with raw sqlite3.Connection
        # We need to access it via the store's internal connection
        # This is a maintenance operation, acceptable to access internals
        try:
            import asyncio
            conn = self.store._conn  # type: ignore[attr-defined]
            if conn is None:
                return {"error": "no connection"}
            result = await asyncio.to_thread(self._cleanup.run_full, conn)
            return result
        except Exception as e:
            log.error("cleanup failed", exc_info=True)
            return {"error": str(e)}

    # -- Stats --

    async def stats(self) -> dict:
        """Get stats from all subsystems."""
        store_count = await self.store.count() if self.store.is_open else 0
        result = {
            "facts": store_count,
            "knowledge_facts": self.knowledge.fact_count,
            "response_cache_size": self.response_cache.size,
            "intent_cache_size": self.intent_cache.size,
            "short_term_turns": self.short_term.size,
        }
        # Add embedding stats if available.
        if self._embedding_index is not None:
            result["embeddings_loaded"] = self._embedding_index.is_loaded
            result["embeddings_count"] = self._embedding_index.count
        # AutoClassifier stats.
        if self._auto_classifier is not None:
            result["classifier_cached"] = self._auto_classifier.get_stats()["cached"]
        # RelationStore stats.
        if self._relation_store is not None:
            try:
                result["relations_count"] = self._relation_store.count()
            except Exception:
                pass
        # EmbeddingCache stats.
        result["embedding_cache"] = self._embedding_cache.get_stats()
        return result
