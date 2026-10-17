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

Auto-Memory (T4.4/T4.5):
    auto_recall         -- Inject relevant memories into LLM context before each call
    auto_save           -- Extract and save facts from conversations (fire-and-forget)
    tool_examples       -- Self-learning: store successful tool-call patterns as examples
    action_logger       -- Log user actions (tool calls) to long-term memory
    session_summary     -- Save session summaries on wake-word timeout (cross-session context)

Personalization (T4.9):
    personal_aliases    -- PersonalAliasStore: user-learned app name aliases (JSON)
    user_profile        -- UserProfile: persistent user facts (name, city, etc.) (JSON)
    usage_stats         -- UsageStats: command/app frequency tracking (JSON)

Tiered Storage (T4.6):
    tier_manager        -- TierManager: hot/warm/cold/archive lifecycle management
    context_inference   -- ContextInference: resolve implicit references from history

Event-Triggered Reminders (T4.8):
    reminders           -- parse_reminder, save_reminder, check_reminders:
                           event-bound reminders that fire on app/game launch

Scheduled (Time-Based) Reminders:
    scheduled_reminders  -- ScheduledReminder, ReminderStore, ReminderScheduler:
                            persistent time-based reminders (SQLite, survives restart)
"""
