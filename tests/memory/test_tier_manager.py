"""Tests for jarvis.memory.tier_manager (T4.6 tiered storage)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.memory.store import MemoryStore
from jarvis.memory.tier_manager import (
    COLD_TO_ARCHIVE_DAYS,
    HOT_TO_WARM_DAYS,
    TierManager,
    WARM_TO_COLD_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_last_accessed(db_path, fact_id: str, dt: datetime) -> None:
    """Directly set last_accessed_at in the DB (bypass store API)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE facts SET last_accessed_at = ? WHERE id = ?",
        (dt.isoformat(), fact_id),
    )
    conn.commit()
    conn.close()


def _get_tier(db_path, fact_id: str) -> str:
    """Read the tier column directly from the DB."""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT tier FROM facts WHERE id = ?", (fact_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else ""


def _get_condensed(db_path, fact_id: str) -> str | None:
    """Read condensed_text directly from the DB."""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT condensed_text FROM facts WHERE id = ?", (fact_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test_tier.db")
    await s.open()
    yield s
    await s.close()


@pytest.fixture()
def tier_mgr(store) -> TierManager:
    return TierManager(store)


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------

class TestTierMigration:
    @pytest.mark.asyncio()
    async def test_new_facts_default_to_hot(self, store) -> None:
        fid = await store.add("some fact", category="general")
        assert _get_tier(store.db_path, fid) == "hot"

    @pytest.mark.asyncio()
    async def test_condensed_text_default_null(self, store) -> None:
        fid = await store.add("another fact")
        assert _get_condensed(store.db_path, fid) is None

    @pytest.mark.asyncio()
    async def test_tier_column_exists_after_migration(self, store) -> None:
        conn = sqlite3.connect(str(store.db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
        conn.close()
        assert "tier" in cols
        assert "condensed_text" in cols

    @pytest.mark.asyncio()
    async def test_fact_dataclass_has_tier(self, store) -> None:
        fid = await store.add("test", category="general")
        fact = await store.get(fid)
        assert fact is not None
        assert fact.tier == "hot"
        assert fact.condensed_text is None


# ---------------------------------------------------------------------------
# Tier demotion
# ---------------------------------------------------------------------------

class TestTierDemotion:
    @pytest.mark.asyncio()
    async def test_hot_to_warm_after_30_days(self, store, tier_mgr) -> None:
        fid = await store.add("A relatively long fact about playing Mafia 2 on Steam")
        # Simulate old access
        old = datetime.now(UTC) - timedelta(days=HOT_TO_WARM_DAYS + 1)
        _set_last_accessed(store.db_path, fid, old)

        counts = await tier_mgr.run_maintenance()
        assert counts["hot_to_warm"] >= 1
        assert _get_tier(store.db_path, fid) == "warm"
        assert _get_condensed(store.db_path, fid) is not None

    @pytest.mark.asyncio()
    async def test_warm_to_cold_after_90_days(self, store, tier_mgr) -> None:
        fid = await store.add("User likes jazz music a lot and listens every evening")
        # Move to warm first
        old_warm = datetime.now(UTC) - timedelta(days=HOT_TO_WARM_DAYS + 1)
        _set_last_accessed(store.db_path, fid, old_warm)
        await tier_mgr.run_maintenance()
        assert _get_tier(store.db_path, fid) == "warm"

        # Now age it past warm threshold
        old_cold = datetime.now(UTC) - timedelta(days=WARM_TO_COLD_DAYS + 1)
        _set_last_accessed(store.db_path, fid, old_cold)
        counts = await tier_mgr.run_maintenance()
        assert counts["warm_to_cold"] >= 1
        assert _get_tier(store.db_path, fid) == "cold"

    @pytest.mark.asyncio()
    async def test_cold_to_archive_after_14_months(self, store, tier_mgr) -> None:
        fid = await store.add("Ancient fact about DOS games")
        # Fast-track to cold
        conn = sqlite3.connect(str(store.db_path))
        conn.execute("UPDATE facts SET tier = 'cold' WHERE id = ?", (fid,))
        conn.commit()
        conn.close()
        # Age past archive threshold
        old = datetime.now(UTC) - timedelta(days=COLD_TO_ARCHIVE_DAYS + 1)
        _set_last_accessed(store.db_path, fid, old)

        counts = await tier_mgr.run_maintenance()
        assert counts["cold_to_archive"] >= 1
        assert _get_tier(store.db_path, fid) == "archive"

    @pytest.mark.asyncio()
    async def test_permanent_facts_not_demoted(self, store, tier_mgr) -> None:
        fid = await store.add("User's name is Serhii", is_permanent=True)
        old = datetime.now(UTC) - timedelta(days=HOT_TO_WARM_DAYS + 100)
        _set_last_accessed(store.db_path, fid, old)

        counts = await tier_mgr.run_maintenance()
        # Permanent facts should NOT be moved
        assert _get_tier(store.db_path, fid) == "hot"
        assert counts["hot_to_warm"] == 0

    @pytest.mark.asyncio()
    async def test_recent_facts_not_demoted(self, store, tier_mgr) -> None:
        fid = await store.add("Just accessed today")
        recent = datetime.now(UTC) - timedelta(hours=1)
        _set_last_accessed(store.db_path, fid, recent)

        counts = await tier_mgr.run_maintenance()
        assert _get_tier(store.db_path, fid) == "hot"
        assert counts["hot_to_warm"] == 0

    @pytest.mark.asyncio()
    async def test_nothing_to_move(self, store, tier_mgr) -> None:
        await store.add("Fresh fact")
        counts = await tier_mgr.run_maintenance()
        assert all(v == 0 for v in counts.values())


# ---------------------------------------------------------------------------
# Tier promotion
# ---------------------------------------------------------------------------

class TestTierPromotion:
    @pytest.mark.asyncio()
    async def test_promote_to_hot_from_warm(self, store) -> None:
        fid = await store.add("Warm fact")
        await store.update_tier(fid, "warm", "Warm fa...")
        assert _get_tier(store.db_path, fid) == "warm"

        await store.promote_to_hot(fid)
        assert _get_tier(store.db_path, fid) == "hot"

    @pytest.mark.asyncio()
    async def test_promote_to_hot_from_cold(self, store) -> None:
        fid = await store.add("Cold fact about something old")
        await store.update_tier(fid, "cold", "Cold fa...")
        assert _get_tier(store.db_path, fid) == "cold"

        await store.promote_to_hot(fid)
        assert _get_tier(store.db_path, fid) == "hot"

    @pytest.mark.asyncio()
    async def test_promote_updates_access_count(self, store) -> None:
        fid = await store.add("Track access")
        # get() already incremented once
        fact_before = await store.get(fid)
        count_before = fact_before.access_count

        await store.promote_to_hot(fid)
        fact_after = await store.get(fid)
        # promote adds 1, get adds 1 → at least count_before + 1 total
        assert fact_after.access_count > count_before

    @pytest.mark.asyncio()
    async def test_promote_refreshes_last_accessed(self, store) -> None:
        fid = await store.add("Access tracking")
        old = datetime.now(UTC) - timedelta(days=60)
        _set_last_accessed(store.db_path, fid, old)

        await store.promote_to_hot(fid)
        fact = await store.get(fid)
        # last_accessed_at should be recent (within last minute)
        la = datetime.fromisoformat(fact.last_accessed_at)
        if la.tzinfo is None:
            la = la.replace(tzinfo=UTC)
        assert (datetime.now(UTC) - la).total_seconds() < 60


# ---------------------------------------------------------------------------
# Condensation
# ---------------------------------------------------------------------------

class TestCondensation:
    def test_condense_for_warm_short_text(self) -> None:
        mgr = TierManager.__new__(TierManager)
        assert mgr._condense_for_warm("short") == "short"

    def test_condense_for_warm_long_text(self) -> None:
        mgr = TierManager.__new__(TierManager)
        text = "x" * 200
        result = mgr._condense_for_warm(text)
        assert len(result) == 100
        assert result.endswith("...")

    def test_condense_for_cold_short(self) -> None:
        mgr = TierManager.__new__(TierManager)
        assert mgr._condense_for_cold("tiny") == "tiny"

    def test_condense_for_cold_long(self) -> None:
        mgr = TierManager.__new__(TierManager)
        text = "y" * 100
        result = mgr._condense_for_cold(text)
        assert len(result) == 50
        assert result.endswith("...")

    def test_condense_for_archive_with_tags(self) -> None:
        mgr = TierManager.__new__(TierManager)
        result = mgr._condense_for_archive("Some very old fact about gaming", ["game", "steam"])
        assert "[game, steam]" in result
        assert len(result) < 80

    def test_condense_for_archive_no_tags(self) -> None:
        mgr = TierManager.__new__(TierManager)
        result = mgr._condense_for_archive("Another old fact that nobody remembers", [])
        assert result.startswith("Another old fact that nobody r")
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# tier_stats
# ---------------------------------------------------------------------------

class TestTierStats:
    @pytest.mark.asyncio()
    async def test_stats_on_empty_db(self, store) -> None:
        stats = await store.tier_stats()
        assert stats == {"hot": 0, "warm": 0, "cold": 0, "archive": 0}

    @pytest.mark.asyncio()
    async def test_stats_counts_tiers(self, store) -> None:
        f1 = await store.add("hot fact 1")
        f2 = await store.add("hot fact 2")
        f3 = await store.add("warm fact")
        await store.update_tier(f3, "warm", "warm...")

        stats = await store.tier_stats()
        assert stats["hot"] == 2
        assert stats["warm"] == 1
        assert stats["cold"] == 0
        assert stats["archive"] == 0
