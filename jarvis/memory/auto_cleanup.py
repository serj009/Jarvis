"""AutoCleanup: deduplication and archival of old unused facts.

Runs periodically (weekly/monthly) to keep memory clean:
1. Deduplicate: remove exact text duplicates, keep the one with highest access_count
2. Archive: remove non-permanent facts older than N days with 0 access
3. Compact: VACUUM the database after cleanup

No FAISS dependency — deduplication is by exact text match.
Future: semantic dedup via embeddings when FAISS is available.

Usage:
    cleanup = AutoCleanup()
    stats = cleanup.run_full(store)
    # stats = {"deduplicated": 3, "archived": 12, "compacted": True}
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)


class AutoCleanup:
    """Memory cleanup: dedup + archival + compact."""

    def __init__(
        self,
        *,
        archive_days: int = 365,
        archive_min_hits: int = 0,
    ) -> None:
        self._archive_days = archive_days
        self._archive_min_hits = archive_min_hits

    def deduplicate(self, conn: sqlite3.Connection) -> int:
        """Remove exact text duplicates. Keep the one with highest access_count.

        Returns number of removed duplicates.
        """
        dupes = conn.execute("""
            SELECT text, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
            FROM facts
            GROUP BY text
            HAVING cnt > 1
        """).fetchall()

        removed = 0
        for row in dupes:
            ids = row[2].split(",")
            if len(ids) < 2:
                continue

            # Keep the one with highest access_count
            best = conn.execute(
                f"SELECT id FROM facts WHERE id IN ({','.join('?' * len(ids))}) "
                "ORDER BY access_count DESC, updated_at DESC LIMIT 1",
                ids,
            ).fetchone()

            if best is None:
                continue

            keep_id = best[0]
            delete_ids = [i for i in ids if i != keep_id]

            # Delete tags first (foreign key)
            for did in delete_ids:
                conn.execute("DELETE FROM fact_tags WHERE fact_id = ?", (did,))
            conn.execute(
                f"DELETE FROM facts WHERE id IN ({','.join('?' * len(delete_ids))})",
                delete_ids,
            )
            removed += len(delete_ids)

        if removed:
            conn.commit()
            log.info("deduplicated: removed %d duplicates", removed)
        return removed

    def archive_old(self, conn: sqlite3.Connection) -> int:
        """Remove non-permanent facts older than N days with low access.

        Returns number of archived (deleted) facts.
        """
        # Find facts to archive
        rows = conn.execute(
            """SELECT id FROM facts
               WHERE is_permanent = 0
               AND access_count <= ?
               AND updated_at < datetime('now', ?)""",
            (self._archive_min_hits, f"-{self._archive_days} days"),
        ).fetchall()

        if not rows:
            return 0

        ids = [r[0] for r in rows]
        for fid in ids:
            conn.execute("DELETE FROM fact_tags WHERE fact_id = ?", (fid,))
        conn.execute(
            f"DELETE FROM facts WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()
        log.info("archived: removed %d old unused facts", len(ids))
        return len(ids)

    def compact(self, conn: sqlite3.Connection) -> None:
        """VACUUM the database to reclaim space."""
        try:
            conn.execute("VACUUM")
            log.info("compacted database")
        except Exception:
            log.warning("compact failed", exc_info=True)

    def integrity_report(
        self, conn: sqlite3.Connection, *, now: str | None = None,
    ) -> dict:
        """Report memory-integrity problems WITHOUT changing anything (Pt 8).

        Pure Python + SQL, no LLM. Surfaces issues for review rather than
        auto-fixing them -- the plan is explicit that a model must never
        silently assign status, and that Python+SQL is enough to *find*
        problems. Returns a dict of issue-lists:

          untagged                -- active fact ids with no tags
          broken_relations        -- relation ids whose endpoint fact is gone
          unresolved_contradictions -- (from_id, to_id) pairs where a
                                       'contradicts' edge links two still-active
                                       facts (one should be superseded)
          expired                 -- active fact ids past their valid_until

        ``now`` is an ISO timestamp used for the expiry check; defaults to
        the current UTC time (injectable for tests).
        """
        now = now or datetime.now(UTC).isoformat()

        untagged = [
            r[0] for r in conn.execute(
                "SELECT id FROM facts f "
                "WHERE f.status != 'superseded' "
                "AND NOT EXISTS (SELECT 1 FROM fact_tags t WHERE t.fact_id = f.id)"
            ).fetchall()
        ]

        broken_relations = [
            {"id": r[0], "from": r[1], "to": r[2], "type": r[3]}
            for r in conn.execute(
                "SELECT id, fact_id_from, fact_id_to, relation_type FROM relations r "
                "WHERE NOT EXISTS (SELECT 1 FROM facts WHERE id = r.fact_id_from) "
                "OR NOT EXISTS (SELECT 1 FROM facts WHERE id = r.fact_id_to)"
            ).fetchall()
        ]

        unresolved_contradictions = [
            {"from": r[0], "to": r[1]}
            for r in conn.execute(
                "SELECT r.fact_id_from, r.fact_id_to FROM relations r "
                "JOIN facts a ON a.id = r.fact_id_from "
                "JOIN facts b ON b.id = r.fact_id_to "
                "WHERE r.relation_type = 'contradicts' "
                "AND a.status = 'active' AND b.status = 'active'"
            ).fetchall()
        ]

        expired = [
            r[0] for r in conn.execute(
                "SELECT id FROM facts "
                "WHERE valid_until IS NOT NULL AND valid_until < ? "
                "AND status = 'active'",
                (now,),
            ).fetchall()
        ]

        report = {
            "untagged": untagged,
            "broken_relations": broken_relations,
            "unresolved_contradictions": unresolved_contradictions,
            "expired": expired,
        }
        total = (
            len(untagged) + len(broken_relations)
            + len(unresolved_contradictions) + len(expired)
        )
        report["total_issues"] = total
        if total:
            log.info("integrity report: %d issue(s) found", total)
        return report

    def prune_broken_relations(self, conn: sqlite3.Connection) -> int:
        """Delete relations whose endpoint fact no longer exists (Pt 8).

        This is the one integrity fix safe to apply automatically: a
        dangling edge points at nothing and can never be useful. Everything
        else in integrity_report is left for review. Returns the count.
        """
        cursor = conn.execute(
            "DELETE FROM relations "
            "WHERE NOT EXISTS (SELECT 1 FROM facts WHERE id = relations.fact_id_from) "
            "OR NOT EXISTS (SELECT 1 FROM facts WHERE id = relations.fact_id_to)"
        )
        conn.commit()
        removed = cursor.rowcount
        if removed:
            log.info("pruned %d broken relation(s)", removed)
        return removed

    def run_full(self, conn: sqlite3.Connection) -> dict:
        """Run full cleanup: dedup + archive + compact."""
        deduped = self.deduplicate(conn)
        archived = self.archive_old(conn)
        if deduped > 0 or archived > 0:
            self.compact(conn)
        return {
            "deduplicated": deduped,
            "archived": archived,
            "compacted": deduped > 0 or archived > 0,
        }
