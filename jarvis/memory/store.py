"""MemoryStore: SQLite-backed persistent fact storage (roadmap T4.1/T4.2/T4.6).

Stores facts as tagged, categorized text entries in a SQLite database.
Designed to scale to 5+ years of daily use with thousands of facts.

Schema:
    facts(id, text, category, source, language, created_at, updated_at,
          access_count, last_accessed_at, is_permanent,
          tier, condensed_text)
    fact_tags(fact_id, tag)

Data path: %APPDATA%/Jarvis/memory/memory.db (Windows)
           ~/.jarvis/memory/memory.db (other)

Thread safety: SQLite is accessed via asyncio.to_thread with one
connection per call. WAL mode is enabled for concurrent reads.

Encryption at rest (T-security):
    Sensitive TEXT columns (text, condensed_text, tags) are encrypted
    with Windows DPAPI via ``jarvis.platform.encrypted_db``.  Metadata
    columns (timestamps, counters, tiers) stay plaintext for indexing.

Embedding hook: when an EmbeddingIndex is attached via
``set_embedding_index()``, add/delete/update operations automatically
keep the FAISS index in sync.

Tiered storage (T4.6):
    Each fact has a ``tier`` column — hot / warm / cold / archive.
    New facts start as ``hot``. TierManager demotes facts based on
    last_accessed_at age. Any access promotes back to ``hot``.
    Original text is NEVER modified; ``condensed_text`` is a separate column.

Usage:
    store = MemoryStore()
    await store.open()
    fact_id = await store.add("User's name is Serhii", category="personal",
                               tags=["name", "user"], language="en")
    facts = await store.search("Serhii")
    await store.close()
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from typing import TYPE_CHECKING

from uuid import uuid4

log = logging.getLogger(__name__)
from jarvis.memory.auto_tagger import AutoTagger
from jarvis.platform.encrypted_db import decrypt_value, encrypt_value

FactSource = Literal["user", "conversation", "system", "import"]

TierLevel = Literal["hot", "warm", "cold", "archive"]

if TYPE_CHECKING:
    from jarvis.memory.embeddings import EmbeddingIndex


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    """A single memory fact."""

    id: str
    text: str
    category: str = "general"
    source: FactSource = "user"
    language: str = "en"
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    access_count: int = 0
    last_accessed_at: str | None = None
    is_permanent: bool = False
    tier: TierLevel = "hot"
    condensed_text: str | None = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _memory_root() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis" / "memory"
    return Path.home() / ".jarvis" / "memory"


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    source          TEXT NOT NULL DEFAULT 'user',
    language        TEXT NOT NULL DEFAULT 'en',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    is_permanent    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fact_tags (
    fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (fact_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_language ON facts(language);
CREATE INDEX IF NOT EXISTS idx_fact_tags_tag ON fact_tags(tag);
"""

# Relations table — graph of typed links between facts.
# Lives in the same DB; schema owned by RelationStore but created here
# so the table exists before any RelationStore method is called.
_RELATIONS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS relations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id_from   TEXT NOT NULL,
    fact_id_to     TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    strength       REAL NOT NULL DEFAULT 1.0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (fact_id_from) REFERENCES facts(id) ON DELETE CASCADE,
    FOREIGN KEY (fact_id_to)   REFERENCES facts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rel_from ON relations(fact_id_from);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relations(fact_id_to);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type);
"""

# FTS5 virtual table for full-text search across fact text.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    text, category, content=facts, content_rowid=rowid
);

-- Triggers to keep FTS in sync with the facts table.
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text, category)
    VALUES (new.rowid, new.text, new.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text, category)
    VALUES ('delete', old.rowid, old.text, old.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text, category)
    VALUES ('delete', old.rowid, old.text, old.category);
    INSERT INTO facts_fts(rowid, text, category)
    VALUES (new.rowid, new.text, new.category);
END;
"""


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """Persistent fact storage backed by SQLite with FTS5 search."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or (_memory_root() / "memory.db")
        self._opened = False
        self._tagger = AutoTagger()
        self._embedding_index: EmbeddingIndex | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def is_open(self) -> bool:
        return self._opened

    def set_embedding_index(self, index: EmbeddingIndex | None) -> None:
        """Attach an EmbeddingIndex so add/delete/update keep it in sync.

        Pass None to detach. Safe to call at any time; the store checks
        ``is_loaded`` before every embedding operation.
        """
        self._embedding_index = index

    # -- lifecycle ----------------------------------------------------------

    async def open(self) -> None:
        """Create the database and schema if needed."""
        if self._opened:
            return
        await asyncio.to_thread(self._sync_open)
        self._opened = True
        log.info("memory store opened: %s", self._db_path)

    def _sync_open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.executescript(_RELATIONS_SCHEMA)
        self._migrate_tiered_columns(conn)
        self._migrate_encrypt_columns(conn)
        try:
            conn.executescript(_FTS_SCHEMA)
        except sqlite3.OperationalError:
            # FTS5 not available (rare); fall back to LIKE search.
            log.warning("FTS5 not available; falling back to LIKE search")
        conn.close()

    async def close(self) -> None:
        self._opened = False
        log.info("memory store closed")

    @staticmethod
    def _migrate_tiered_columns(conn: sqlite3.Connection) -> None:
        """Add tier + condensed_text columns if they don't exist (T4.6 migration).

        SQLite supports ``ALTER TABLE ... ADD COLUMN`` but not CHECK constraints
        in ALTER. The CHECK is enforced at the application level.
        """
        # Inspect existing columns
        cursor = conn.execute("PRAGMA table_info(facts)")
        existing = {row[1] for row in cursor.fetchall()}

        if "tier" not in existing:
            conn.execute(
                "ALTER TABLE facts ADD COLUMN tier TEXT NOT NULL DEFAULT 'hot'"
            )
            log.info("migration: added 'tier' column to facts table")

        if "condensed_text" not in existing:
            conn.execute(
                "ALTER TABLE facts ADD COLUMN condensed_text TEXT"
            )
            log.info("migration: added 'condensed_text' column to facts table")

        # Index for tier-based queries (maintenance, auto-recall filtering)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_tier ON facts(tier)"
        )
        conn.commit()

    @staticmethod
    def _migrate_encrypt_columns(conn: sqlite3.Connection) -> None:
        """One-time migration: encrypt plaintext fact text/condensed_text.

        Scans all rows; any whose ``text`` column does NOT start with the
        ``dpapi:`` scheme prefix is considered legacy plaintext and is
        encrypted in place.  Tags in ``fact_tags`` are also encrypted.

        The migration is idempotent — already-encrypted values pass
        through ``encrypt_value`` unchanged.
        """
        from jarvis.platform.encrypted_db import encrypt_value, needs_migration
        from jarvis.platform.secrets import dpapi_available

        if not dpapi_available():
            return  # nothing to do on non-Windows

        rows = conn.execute(
            "SELECT id, text, condensed_text FROM facts"
        ).fetchall()
        migrated = 0
        for row_id, text, condensed in rows:
            new_text = encrypt_value(text) if needs_migration(text) else text
            new_cond = (
                encrypt_value(condensed)
                if condensed is not None and needs_migration(condensed)
                else condensed
            )
            if new_text != text or new_cond != condensed:
                conn.execute(
                    "UPDATE facts SET text = ?, condensed_text = ? WHERE id = ?",
                    (new_text, new_cond, row_id),
                )
                migrated += 1

        # Encrypt tags
        tag_rows = conn.execute("SELECT fact_id, tag FROM fact_tags").fetchall()
        for fid, tag in tag_rows:
            if needs_migration(tag):
                new_tag = encrypt_value(tag)
                conn.execute(
                    "UPDATE fact_tags SET tag = ? WHERE fact_id = ? AND tag = ?",
                    (new_tag, fid, tag),
                )

        if migrated:
            conn.commit()
            log.info("migration: encrypted %d fact rows", migrated)

    # -- CRUD ---------------------------------------------------------------

    async def add(
        self,
        text: str,
        *,
        category: str = "general",
        source: FactSource = "user",
        language: str = "en",
        tags: list[str] | None = None,
        is_permanent: bool = False,
    ) -> str:
        """Add a new fact with automatic tagging. Returns the fact ID.

        AutoTagger enriches the fact: detects language, extracts tags
        from text (game titles, tech terms, personal info), and refines
        the category. User-provided tags are merged with auto-detected ones.
        """
        fact_id = uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        # Auto-tag: enrich category, tags, and language from text content
        auto = self._tagger.analyze(text, category_hint=category)
        category = auto.category
        language = language or auto.language
        merged_tags = list(set((tags or []) + auto.tags))
        await asyncio.to_thread(
            self._sync_add, fact_id, text, category, source,
            language, merged_tags, now, is_permanent,
        )
        await self._maybe_embed(fact_id, text)
        log.info("fact added: %s (category=%s, tags=%s)", fact_id, category, tags)
        return fact_id

    async def _maybe_embed(self, fact_id: str, text: str) -> None:
        """Add embedding for a fact if the index is available."""
        idx = self._embedding_index
        if idx is not None and idx.is_loaded:
            idx.add(fact_id, text)

    def _sync_add(
        self, fact_id: str, text: str, category: str, source: str,
        language: str, tags: list[str], now: str, is_permanent: bool,
    ) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            enc_text = encrypt_value(text)
            conn.execute(
                "INSERT INTO facts (id, text, category, source, language, "
                "created_at, updated_at, is_permanent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fact_id, enc_text, category, source, language, now, now, int(is_permanent)),
            )
            for tag in tags:
                enc_tag = encrypt_value(tag.lower().strip())
                conn.execute(
                    "INSERT OR IGNORE INTO fact_tags (fact_id, tag) VALUES (?, ?)",
                    (fact_id, enc_tag),
                )
            conn.commit()
        finally:
            conn.close()

    async def get(self, fact_id: str) -> Fact | None:
        """Get a fact by ID, incrementing access_count."""
        return await asyncio.to_thread(self._sync_get, fact_id)

    def _sync_get(self, fact_id: str) -> Fact | None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE facts SET access_count = access_count + 1, "
                "last_accessed_at = ? WHERE id = ?",
                (now, fact_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
            if row is None:
                return None
            enc_tags = [r[0] for r in conn.execute(
                "SELECT tag FROM fact_tags WHERE fact_id = ?", (fact_id,)
            ).fetchall()]
            tags = [decrypt_value(t) or t for t in enc_tags]
            return self._row_to_fact(row, tags)
        finally:
            conn.close()

    async def delete(self, fact_id: str) -> bool:
        """Delete a fact. Returns True if it existed."""
        deleted = await asyncio.to_thread(self._sync_delete, fact_id)
        if deleted:
            self._maybe_remove_embedding(fact_id)
        return deleted

    def _maybe_remove_embedding(self, fact_id: str) -> None:
        """Remove embedding for a fact if the index is available."""
        idx = self._embedding_index
        if idx is not None and idx.is_loaded:
            idx.remove(fact_id)

    def _sync_delete(self, fact_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def update(self, fact_id: str, text: str) -> bool:
        """Update a fact's text. Returns True if it existed."""
        now = datetime.now(UTC).isoformat()
        updated = await asyncio.to_thread(self._sync_update, fact_id, text, now)
        if updated:
            await self._maybe_embed(fact_id, text)
        return updated

    async def update_category(self, fact_id: str, category: str) -> bool:
        """Update a fact's category. Returns True if it existed.

        Used by AutoClassifier integration: when LLM classifies an entity,
        the fact's category is updated to match the classification.
        """
        now = datetime.now(UTC).isoformat()
        return await asyncio.to_thread(self._sync_update_category, fact_id, category, now)

    def _sync_update_category(self, fact_id: str, category: str, now: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(
                "UPDATE facts SET category = ?, updated_at = ? WHERE id = ?",
                (category, now, fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # -- embedding helpers (also used by MemoryManager for rebuild) ---------

    # _maybe_embed and _maybe_remove_embedding are defined above.

    async def all_facts_for_embedding(self) -> list[tuple[str, str]]:
        """Return all (fact_id, text) pairs for embedding index rebuild.

        Lightweight: only fetches id and text columns, no tags.
        Used by MemoryManager to rebuild the FAISS index on first load
        when no index file exists on disk.
        """
        return await asyncio.to_thread(self._sync_all_facts_for_embedding)

    def _sync_all_facts_for_embedding(self) -> list[tuple[str, str]]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute("SELECT id, text FROM facts").fetchall()
            return [(r[0], decrypt_value(r[1]) or r[1]) for r in rows]
        finally:
            conn.close()

    def _sync_update(self, fact_id: str, text: str, now: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            enc_text = encrypt_value(text)
            cursor = conn.execute(
                "UPDATE facts SET text = ?, updated_at = ? WHERE id = ?",
                (enc_text, now, fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # -- search -------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
        tag: str | None = None,
        limit: int = 10,
    ) -> list[Fact]:
        """Search facts by text (FTS5 or LIKE fallback), category, or tag."""
        return await asyncio.to_thread(
            self._sync_search, query, category, tag, limit,
        )

    def _sync_search(
        self, query: str, category: str | None,
        tag: str | None, limit: int,
    ) -> list[Fact]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            # Try FTS5 first
            try:
                return self._fts_search(conn, query, category, tag, limit)
            except sqlite3.OperationalError:
                return self._like_search(conn, query, category, tag, limit)
        finally:
            conn.close()

    def _fts_search(
        self, conn: sqlite3.Connection, query: str,
        category: str | None, tag: str | None, limit: int,
    ) -> list[Fact]:
        # FTS5 match query
        sql = """
            SELECT f.*, rank
            FROM facts f
            JOIN facts_fts fts ON f.rowid = fts.rowid
            WHERE facts_fts MATCH ?
        """
        params: list = [query]
        if category:
            sql += " AND f.category = ?"
            params.append(category)
        if tag:
            sql += " AND f.id IN (SELECT fact_id FROM fact_tags WHERE tag = ?)"
            params.append(encrypt_value(tag.lower()) or tag.lower())
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            # row has extra 'rank' column at the end
            fact_row = row[:-1]
            enc_tags = [r[0] for r in conn.execute(
                "SELECT tag FROM fact_tags WHERE fact_id = ?", (fact_row[0],)
            ).fetchall()]
            result.append(self._row_to_fact(fact_row, [decrypt_value(t) or t for t in enc_tags]))
        return result

    def _like_search(
        self, conn: sqlite3.Connection, query: str,
        category: str | None, tag: str | None, limit: int,
    ) -> list[Fact]:
        sql = "SELECT * FROM facts WHERE text LIKE ?"
        params: list = [f"%{query}%"]
        if category:
            sql += " AND category = ?"
            params.append(category)
        if tag:
            sql += " AND id IN (SELECT fact_id FROM fact_tags WHERE tag = ?)"
            params.append(encrypt_value(tag.lower()) or tag.lower())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            enc_tags = [r[0] for r in conn.execute(
                "SELECT tag FROM fact_tags WHERE fact_id = ?", (row[0],)
            ).fetchall()]
            result.append(self._row_to_fact(row, [decrypt_value(t) or t for t in enc_tags]))
        return result

    # -- stats --------------------------------------------------------------

    async def count(self, category: str | None = None) -> int:
        """Count total facts, optionally filtered by category."""
        return await asyncio.to_thread(self._sync_count, category)

    def _sync_count(self, category: str | None) -> int:
        conn = sqlite3.connect(str(self._db_path))
        try:
            if category:
                row = conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE category = ?", (category,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    async def categories(self) -> list[tuple[str, int]]:
        """Return all categories with fact counts."""
        return await asyncio.to_thread(self._sync_categories)

    def _sync_categories(self) -> list[tuple[str, int]]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT category, COUNT(*) FROM facts GROUP BY category ORDER BY COUNT(*) DESC"
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        finally:
            conn.close()

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_fact(row: tuple, tags: list[str]) -> Fact:
        # Columns: id(0) text(1) category(2) source(3) language(4)
        # created_at(5) updated_at(6) access_count(7) last_accessed_at(8)
        # is_permanent(9) [tier(10) condensed_text(11)]
        raw_text = row[1]
        raw_condensed = row[11] if len(row) > 11 else None
        return Fact(
            id=row[0],
            text=decrypt_value(raw_text) or raw_text,
            category=row[2],
            source=row[3],
            language=row[4],
            created_at=row[5],
            updated_at=row[6],
            access_count=row[7],
            last_accessed_at=row[8],
            is_permanent=bool(row[9]),
            tier=row[10] if len(row) > 10 and row[10] else "hot",
            condensed_text=(
                decrypt_value(raw_condensed) if raw_condensed else None
            ),
            tags=tags,
        )

    # -- tiered storage (T4.6) ----------------------------------------------

    async def get_facts_by_tier_and_age(
        self, tier: TierLevel, before: datetime,
    ) -> list[Fact]:
        """Get facts in *tier* whose last_accessed_at is older than *before*.

        Permanent facts are excluded — they never transition tiers.
        Facts with NULL last_accessed_at use created_at as the fallback.
        """
        return await asyncio.to_thread(
            self._sync_get_facts_by_tier_and_age, tier, before.isoformat(),
        )

    def _sync_get_facts_by_tier_and_age(
        self, tier: str, before_iso: str,
    ) -> list[Fact]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                """
                SELECT * FROM facts
                WHERE tier = ?
                  AND is_permanent = 0
                  AND COALESCE(last_accessed_at, created_at) < ?
                ORDER BY COALESCE(last_accessed_at, created_at) ASC
                """,
                (tier, before_iso),
            ).fetchall()
            result = []
            for row in rows:
                enc_tags = [
                    r[0]
                    for r in conn.execute(
                        "SELECT tag FROM fact_tags WHERE fact_id = ?", (row[0],)
                    ).fetchall()
                ]
                result.append(self._row_to_fact(row, [decrypt_value(t) or t for t in enc_tags]))
            return result
        finally:
            conn.close()

    async def update_tier(
        self, fact_id: str, new_tier: TierLevel, condensed: str,
    ) -> None:
        """Move a fact to *new_tier* and set its *condensed_text*."""
        await asyncio.to_thread(
            self._sync_update_tier, fact_id, new_tier, condensed,
        )

    def _sync_update_tier(
        self, fact_id: str, new_tier: str, condensed: str,
    ) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            condensed = encrypt_value(condensed) or condensed
            conn.execute(
                "UPDATE facts SET tier = ?, condensed_text = ? WHERE id = ?",
                (new_tier, condensed, fact_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def promote_to_hot(self, fact_id: str) -> None:
        """Promote a fact back to HOT tier and refresh last_accessed_at.

        Called by auto-recall whenever a fact is matched — ensures that
        actively-used memories stay in the hottest tier regardless of age.
        """
        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(self._sync_promote_to_hot, fact_id, now)

    def _sync_promote_to_hot(self, fact_id: str, now: str) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "UPDATE facts SET tier = 'hot', last_accessed_at = ?, "
                "access_count = access_count + 1 WHERE id = ?",
                (now, fact_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def tier_stats(self) -> dict[str, int]:
        """Return fact counts per tier — {hot: N, warm: N, cold: N, archive: N}."""
        return await asyncio.to_thread(self._sync_tier_stats)

    def _sync_tier_stats(self) -> dict[str, int]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT tier, COUNT(*) FROM facts GROUP BY tier"
            ).fetchall()
            stats = {"hot": 0, "warm": 0, "cold": 0, "archive": 0}
            for tier, cnt in rows:
                stats[tier] = cnt
            return stats
        finally:
            conn.close()
