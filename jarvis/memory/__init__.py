"""Jarvis persistent memory subsystem (Phase 4: T4.1-T4.3, full architecture).

Core:
    store               -- MemoryStore: SQLite CRUD with FTS5 + AutoTagger
    conversation        -- ConversationMemory: quarterly rotation + session tracking
    memory_manager      -- MemoryManager: unified facade for all subsystems

Sharding:
    shard_manager       -- ShardManager: per-category .db files + normalize_topic()
    dynamic_shard_manager -- DynamicShardManager: auto-promotion when ≥10 facts

Intelligence:
    auto_tagger         -- AutoTagger: regex tag/category/language extraction
    auto_classifier     -- AutoClassifier: LLM-based entity classification (shard routing)
    smart_search        -- SmartSearch: 6-signal ranked search (keyword + semantic)
    auto_cleanup        -- AutoCleanup: deduplication + archival
    embeddings          -- EmbeddingIndex: FAISS + sentence-transformers semantic search
    embedding_cache     -- EmbeddingCache: LRU cache for embedding vectors

Relations:
    relations           -- RelationStore: graph of typed relations between facts

Caching:
    cache               -- ResponseCache, IntentCache, ShortTermMemory
    predictive_preload  -- PredictivePreload: context-aware preloading

Knowledge:
    knowledge_store     -- KnowledgeStore: read-only YAML knowledge base

Voice Tools:
    tools               -- remember, recall, forget, memory status
"""
