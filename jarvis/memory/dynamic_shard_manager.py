"""DynamicShardManager: auto-promotes categories to their own shard .db.

Extends ShardManager with automatic shard creation: when a normalized
topic accumulates >= SHARD_THRESHOLD facts in the 'general' shard,
those facts get migrated to a dedicated .db file.

Fragmentation protection via normalize_topic() from shard_manager.py:
"diablo", "cuphead", "baldurs_gate" all map to "games", not 3 shards.

Registry of dynamic shards is persisted to shard_registry.json so
auto-created shards survive restarts.

Usage:
    dsm = DynamicShardManager(shard_manager)
    dsm.check_and_promote()  # called periodically or after N inserts
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from pathlib import Path

from jarvis.memory.shard_manager import ShardManager, normalize_topic

log = logging.getLogger(__name__)

# Standard shards that always exist (never auto-created)
STANDARD_SHARDS = frozenset({"general", "personal", "tech", "work", "media"})

# Minimum facts for a topic to get its own shard
SHARD_THRESHOLD = 10

# Check frequency: run promotion check every N add() calls
CHECK_EVERY_N = 20


class DynamicShardManager:
    """Auto-promotes topics with enough facts to dedicated shards.

    Wraps a ShardManager and tracks which dynamic shards were created.
    Persists the registry to JSON so it survives restarts.
    """

    def __init__(
        self,
        shard_manager: ShardManager,
        registry_path: Path | None = None,
    ) -> None:
        self._shards = shard_manager
        self._registry_path = registry_path or (shard_manager.root / "shard_registry.json")
        self._dynamic_shards: dict[str, dict] = {}
        self._insert_count = 0
        self._load_registry()

    def _load_registry(self) -> None:
        if self._registry_path.exists():
            try:
                with self._registry_path.open("r", encoding="utf-8") as f:
                    self._dynamic_shards = json.load(f)
                log.info("dynamic shard registry: %d shards", len(self._dynamic_shards))
            except Exception:
                log.warning("shard registry load failed", exc_info=True)

    def _save_registry(self) -> None:
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            with self._registry_path.open("w", encoding="utf-8") as f:
                json.dump(self._dynamic_shards, f, ensure_ascii=False, indent=2)
        except Exception:
            log.warning("shard registry save failed", exc_info=True)

    def resolve_shard(self, category: str, tags: list[str] | None = None) -> str:
        """Determine which shard a fact belongs to.

        Normalizes category + tags via normalize_topic(), then checks
        if a dynamic or standard shard exists for the result.
        Falls back to 'general'.
        """
        candidates = set()

        # Normalize the category
        norm_cat = normalize_topic("", category)
        candidates.add(norm_cat)

        # Normalize all tags
        for tag in (tags or []):
            candidates.add(normalize_topic(tag, "general"))

        # Check dynamic shards first (user interests)
        for topic in candidates:
            if topic in self._dynamic_shards:
                return topic

        # Check standard shards
        for topic in candidates:
            if topic in STANDARD_SHARDS:
                return topic

        # Default
        return norm_cat if norm_cat in STANDARD_SHARDS else "general"

    def on_fact_added(self) -> list[str]:
        """Call after each fact insertion. Periodically triggers promotion check.

        Returns list of newly created shard names (usually empty).
        """
        self._insert_count += 1
        if self._insert_count % CHECK_EVERY_N == 0:
            return self.check_and_promote()
        return []

    def check_and_promote(self) -> list[str]:
        """Scan the 'general' shard for topics that deserve their own shard.

        Counts normalized topics across all facts in general.db.
        If any topic has >= SHARD_THRESHOLD facts, creates a shard
        and migrates those facts.

        Returns list of newly created shard names.
        """
        try:
            conn = self._shards.connect("general")
        except Exception:
            return []

        try:
            rows = conn.execute(
                "SELECT id, text, category FROM facts"
            ).fetchall()
        except Exception:
            conn.close()
            return []

        # Count facts per normalized topic
        topic_counter: Counter[str] = Counter()
        fact_topics: dict[str, set[str]] = {}  # fact_id -> set of topics

        for row in rows:
            fact_id = row[0]
            text = row[1] or ""
            category = row[2] or "general"

            norm = normalize_topic(text, category)
            if norm not in STANDARD_SHARDS:
                topic_counter[norm] += 1
                fact_topics.setdefault(fact_id, set()).add(norm)

        # Also check tags
        try:
            tag_rows = conn.execute("SELECT fact_id, tag FROM fact_tags").fetchall()
            for fact_id, tag in tag_rows:
                norm = normalize_topic(tag, "general")
                if norm not in STANDARD_SHARDS:
                    topic_counter[norm] += 1
                    fact_topics.setdefault(fact_id, set()).add(norm)
        except Exception:
            pass

        conn.close()

        # Promote topics that exceed threshold
        created = []
        for topic, count in topic_counter.items():
            if (
                count >= SHARD_THRESHOLD
                and topic not in self._dynamic_shards
                and topic not in STANDARD_SHARDS
            ):
                self._promote(topic, fact_topics)
                created.append(topic)

        return created

    def _promote(self, topic: str, fact_topics: dict[str, set[str]]) -> None:
        """Create a dynamic shard and migrate matching facts."""
        log.info("promoting topic %r to dynamic shard", topic)

        # Register the shard
        self._dynamic_shards[topic] = {
            "reason": f"auto: topic reached threshold",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_registry()

        # Ensure the shard .db exists
        self._shards.ensure_shard(topic)

        # Migrate facts from general -> new shard
        fact_ids = [fid for fid, topics in fact_topics.items() if topic in topics]
        if not fact_ids:
            return

        try:
            src = self._shards.connect("general")
            dst = self._shards.connect(topic)

            migrated = 0
            for fid in fact_ids:
                row = src.execute(
                    "SELECT * FROM facts WHERE id = ?", (fid,)
                ).fetchone()
                if row is None:
                    continue

                cols = [desc[0] for desc in src.execute("SELECT * FROM facts LIMIT 0").description]
                vals = [row[i] for i in range(len(cols))]
                placeholders = ",".join("?" * len(cols))
                col_names = ",".join(cols)

                dst.execute(f"INSERT OR IGNORE INTO facts ({col_names}) VALUES ({placeholders})", vals)

                # Migrate tags
                tags = src.execute(
                    "SELECT tag FROM fact_tags WHERE fact_id = ?", (fid,)
                ).fetchall()
                for (tag,) in tags:
                    dst.execute(
                        "INSERT OR IGNORE INTO fact_tags (fact_id, tag) VALUES (?, ?)",
                        (fid, tag),
                    )

                # Delete from source
                src.execute("DELETE FROM fact_tags WHERE fact_id = ?", (fid,))
                src.execute("DELETE FROM facts WHERE id = ?", (fid,))
                migrated += 1

            src.commit()
            dst.commit()
            src.close()
            dst.close()
            log.info("migrated %d facts: general -> %s", migrated, topic)
        except Exception:
            log.error("migration failed for topic %r", topic, exc_info=True)

    def force_create_shard(self, name: str, description: str = "") -> None:
        """Manually create a dynamic shard."""
        normalized = normalize_topic(name, "general")
        if normalized in self._dynamic_shards or normalized in STANDARD_SHARDS:
            return
        self._dynamic_shards[normalized] = {
            "reason": description or "manually created",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_registry()
        self._shards.ensure_shard(normalized)
        log.info("manually created shard: %s", normalized)

    @property
    def dynamic_shards(self) -> dict[str, dict]:
        return dict(self._dynamic_shards)

    def all_shards_info(self) -> dict:
        """Summary of all shards for diagnostics."""
        return {
            "standard": sorted(STANDARD_SHARDS),
            "dynamic": dict(self._dynamic_shards),
            "all_active": self._shards.list_shards(),
        }
