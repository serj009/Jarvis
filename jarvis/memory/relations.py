"""RelationStore: graph of relations between memory facts.

Stores and traverses typed relations between facts in the same SQLite
database as MemoryStore. Enables rich queries like "what do I know about
Stalker" → finds the game fact, then traverses belongs_to / related_to
edges to surface achievements, locations, weapons, etc.

Relation types:
    belongs_to  — "Achievement Doslidnyk" belongs_to "Stalker 2"
    related_to  — "Achievement Doslidnyk" related_to "Achievement Voyin"
    depends_on  — "Phase 3" depends_on "Phase 2"
    contradicts — "Fact A" contradicts "Fact B" (conflict)
    part_of     — "Chapter 3" part_of "Book X"

Thread safety: each method opens and closes its own sqlite3.Connection
(same pattern as MemoryStore). WAL mode enables concurrent reads.

Usage:
    relations = RelationStore(db_path=Path("memory.db"))
    relations.init_schema()
    rel_id = relations.add_relation(from_id, to_id, "belongs_to", strength=0.9)
    related = relations.get_related(fact_id, depth=2)
    all_ids = relations.find_all_related_to_tag("stalker_2")
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema (added to the same DB as facts/fact_tags)
# ---------------------------------------------------------------------------

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


class RelationStore:
    """Graph of typed relations between memory facts.

    Shares the same SQLite database as MemoryStore (facts + fact_tags).
    Each method creates its own connection following the repo pattern.
    """

    RELATION_TYPES = frozenset({
        "belongs_to", "related_to", "depends_on", "contradicts", "part_of",
    })

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """Create a new connection with WAL mode and row_factory."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        """Create the relations table if it doesn't exist.

        Safe to call multiple times (uses IF NOT EXISTS).
        Called from MemoryStore._sync_open() or MemoryManager.open().
        """
        conn = self._connect()
        try:
            conn.executescript(_RELATIONS_SCHEMA)
            conn.commit()
            log.debug("relations schema initialized")
        finally:
            conn.close()

    # -- CRUD ---------------------------------------------------------------

    def add_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str,
        strength: float = 1.0,
    ) -> int:
        """Add a relation between two facts. Returns the relation ID.

        Warns (but does not reject) unknown relation types for forward
        compatibility.
        """
        if relation_type not in self.RELATION_TYPES:
            log.warning("unknown relation type: %s", relation_type)

        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO relations (fact_id_from, fact_id_to, "
                "relation_type, strength) VALUES (?, ?, ?, ?)",
                (from_id, to_id, relation_type, strength),
            )
            conn.commit()
            rel_id = cursor.lastrowid
            log.debug(
                "relation added: %s -[%s]-> %s (id=%d)",
                from_id, relation_type, to_id, rel_id,
            )
            return rel_id
        finally:
            conn.close()

    def remove_relation(self, relation_id: int) -> bool:
        """Remove a relation by ID. Returns True if it existed."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM relations WHERE id = ?", (relation_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_related(
        self,
        fact_id: str,
        relation_type: Optional[str] = None,
        depth: int = 1,
    ) -> list[dict]:
        """Find related facts via graph traversal.

        depth=1: direct relations only.
        depth=2: relations of relations (2-hop graph traversal).

        Returns a list of dicts with relation info and related fact content.
        Traverses both forward and reverse edges (undirected).
        """
        conn = self._connect()
        try:
            visited: set[str] = set()
            results: list[dict] = []
            self._traverse(conn, fact_id, relation_type, depth, visited, results, 0)
            return results
        finally:
            conn.close()

    def _traverse(
        self,
        conn: sqlite3.Connection,
        fact_id: str,
        relation_type: Optional[str],
        max_depth: int,
        visited: set[str],
        results: list[dict],
        current_depth: int,
    ) -> None:
        """Recursive graph traversal (BFS-style with depth tracking)."""
        if current_depth >= max_depth or fact_id in visited:
            return
        visited.add(fact_id)

        # Forward edges: fact_id -> related
        query_fwd = (
            "SELECT r.id, r.fact_id_from, r.fact_id_to, r.relation_type, "
            "r.strength, r.created_at, f.text AS related_content "
            "FROM relations r "
            "LEFT JOIN facts f ON r.fact_id_to = f.id "
            "WHERE r.fact_id_from = ?"
        )
        params_fwd: list = [fact_id]
        if relation_type:
            query_fwd += " AND r.relation_type = ?"
            params_fwd.append(relation_type)

        for row in conn.execute(query_fwd, params_fwd).fetchall():
            results.append(dict(row))
            self._traverse(
                conn, row["fact_id_to"], relation_type,
                max_depth, visited, results, current_depth + 1,
            )

        # Reverse edges: related -> fact_id
        query_rev = (
            "SELECT r.id, r.fact_id_from, r.fact_id_to, r.relation_type, "
            "r.strength, r.created_at, f.text AS related_content "
            "FROM relations r "
            "LEFT JOIN facts f ON r.fact_id_from = f.id "
            "WHERE r.fact_id_to = ?"
        )
        params_rev: list = [fact_id]
        if relation_type:
            query_rev += " AND r.relation_type = ?"
            params_rev.append(relation_type)

        for row in conn.execute(query_rev, params_rev).fetchall():
            results.append(dict(row))
            # Do NOT recurse reverse edges to prevent infinite loops;
            # the forward traversal of the opposite node handles it.

    def find_all_related_to_tag(self, tag: str) -> list[str]:
        """Find all fact IDs related to a tag (through relation chains).

        First finds facts tagged with the given tag (via fact_tags table),
        then traverses the relation graph (depth=2) to find connected facts.
        Returns a sorted list of unique fact IDs.
        """
        conn = self._connect()
        try:
            # Find facts with this tag
            rows = conn.execute(
                "SELECT fact_id FROM fact_tags WHERE tag = ?",
                (tag.lower().strip(),),
            ).fetchall()

            all_ids: set[str] = {row["fact_id"] for row in rows}

            # Expand through relation graph (depth=2)
            for fact_id in list(all_ids):
                visited: set[str] = set()
                related: list[dict] = []
                self._traverse(conn, fact_id, None, 2, visited, related, 0)
                for rel in related:
                    fid_from = rel.get("fact_id_from", "")
                    fid_to = rel.get("fact_id_to", "")
                    if fid_from:
                        all_ids.add(fid_from)
                    if fid_to:
                        all_ids.add(fid_to)

            return sorted(all_ids)
        finally:
            conn.close()

    def get_relations_for_fact(self, fact_id: str) -> list[dict]:
        """Get all direct relations involving a fact (both directions)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT r.*, f1.text AS from_content, f2.text AS to_content "
                "FROM relations r "
                "LEFT JOIN facts f1 ON r.fact_id_from = f1.id "
                "LEFT JOIN facts f2 ON r.fact_id_to = f2.id "
                "WHERE r.fact_id_from = ? OR r.fact_id_to = ?",
                (fact_id, fact_id),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        """Total number of relations."""
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM relations").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
