"""Tests for jarvis.memory.proactive_engine (Layer 1 foundation)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.memory.proactive_engine import (
    InitiativeLevel,
    ProactiveEngine,
    ProactivePatternStore,
    classify_leisure,
    jaccard,
)


# ── fixtures ───────────────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path):
    s = ProactivePatternStore(db_path=tmp_path / "proactive.db")
    s.open()
    yield s
    s.close()


def _config(**over):
    base = dict(
        enabled=True,
        initiative_level="high",
        check_interval_seconds=60,
        work_hours_threshold=4.0,
        leisure_hours_threshold=2.0,
        boot_similarity_threshold=0.7,
        dismiss_downgrade_count=3,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeBus:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)


# ── InitiativeLevel ────────────────────────────────────────────────

class TestInitiativeLevel:
    def test_from_str(self):
        assert InitiativeLevel.from_str("medium") is InitiativeLevel.MEDIUM
        assert InitiativeLevel.from_str("GARBAGE") is InitiativeLevel.HIGH

    def test_downgrade_chain(self):
        assert InitiativeLevel.HIGH.downgraded() is InitiativeLevel.MEDIUM
        assert InitiativeLevel.MEDIUM.downgraded() is InitiativeLevel.LOW
        assert InitiativeLevel.LOW.downgraded() is InitiativeLevel.SILENT
        assert InitiativeLevel.SILENT.downgraded() is InitiativeLevel.SILENT  # floor

    def test_upgrade_chain(self):
        assert InitiativeLevel.SILENT.upgraded() is InitiativeLevel.LOW
        assert InitiativeLevel.HIGH.upgraded() is InitiativeLevel.HIGH  # ceiling


# ── pattern store ──────────────────────────────────────────────────

class TestPatternStore:
    def test_record_creates_then_increments(self, store):
        assert store.record("boot_time", "08_Mon") == 1
        assert store.record("boot_time", "08_Mon") == 2
        assert store.record("boot_time", "08_Mon") == 3

    def test_separate_keys_independent(self, store):
        store.record("dismissed", "a")
        store.record("dismissed", "b")
        store.record("dismissed", "b")
        assert store.get("dismissed", "a")["count"] == 1
        assert store.get("dismissed", "b")["count"] == 2

    def test_value_json_roundtrip(self, store):
        store.record("boot_sequence", "seq1", value=["chrome", "spotify"])
        row = store.get("boot_sequence", "seq1")
        assert row["value"] == ["chrome", "spotify"]

    def test_list_type_sorted_by_count(self, store):
        store.record("command_after", "git→x")
        store.record("command_after", "git→y")
        store.record("command_after", "git→y")
        rows = store.list_type("command_after")
        assert rows[0]["key"] == "git→y"

    def test_get_missing_returns_none(self, store):
        assert store.get("boot_time", "nope") is None


# ── leisure classification + jaccard ───────────────────────────────

class TestHelpers:
    def test_classify_game(self):
        assert classify_leisure("Counter-Strike — Steam") == "game"

    def test_classify_video(self):
        assert classify_leisure("YouTube — Chrome") == "video"

    def test_classify_none(self):
        assert classify_leisure("report.docx — Word") is None
        assert classify_leisure("") is None

    def test_jaccard(self):
        assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
        assert jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1 / 3)
        assert jaccard(set(), set()) == 0.0


# ── engine: levels + persistence ───────────────────────────────────

class TestEngineLevels:
    def test_reads_level_from_config(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="low"), store=store)
        assert eng.level is InitiativeLevel.LOW

    def test_set_level_persists_to_config(self, store):
        cfg = _config()
        changed = []
        eng = ProactiveEngine(
            config=cfg, store=store, on_level_change=lambda: changed.append(1)
        )
        eng.set_level(InitiativeLevel.SILENT)
        assert cfg.initiative_level == "silent"
        assert changed  # on_level_change fired

    def test_raise_and_lower(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="medium"), store=store)
        assert eng.lower_level() is InitiativeLevel.LOW
        assert eng.raise_level() is InitiativeLevel.MEDIUM


# ── engine: adaptive dismiss ───────────────────────────────────────

class TestAdaptiveDismiss:
    def test_three_dismissals_downgrade(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="high"), store=store)
        eng.record_dismiss("proj_x")
        eng.record_dismiss("proj_x")
        assert eng.level is InitiativeLevel.HIGH  # not yet
        eng.record_dismiss("proj_x")
        assert eng.level is InitiativeLevel.MEDIUM  # downgraded on the 3rd

    def test_accept_resets_streak(self, store):
        eng = ProactiveEngine(config=_config(), store=store)
        eng.record_dismiss("x")
        eng.record_dismiss("x")
        eng.record_accept()  # resets
        eng.record_dismiss("x")
        assert eng.level is InitiativeLevel.HIGH  # streak was reset

    def test_per_suggestion_dismiss_count(self, store):
        eng = ProactiveEngine(config=_config(), store=store)
        eng.record_dismiss("proj_x")
        eng.record_dismiss("proj_x")
        assert eng.dismiss_count_for("proj_x") == 2

    def test_silent_is_downgrade_floor(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="silent"), store=store)
        for _ in range(3):
            eng.record_dismiss("x")
        assert eng.level is InitiativeLevel.SILENT


# ── engine: speak gate ─────────────────────────────────────────────

class TestSpeakGate:
    def test_high_allows_everything(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="high"), store=store)
        assert eng.may_speak("normal")
        assert eng.may_speak("important")
        assert eng.may_speak("critical")

    def test_medium_blocks_normal(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="medium"), store=store)
        assert not eng.may_speak("normal")
        assert eng.may_speak("important")

    def test_low_only_critical(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="low"), store=store)
        assert not eng.may_speak("important")
        assert eng.may_speak("critical")

    def test_silent_blocks_all(self, store):
        eng = ProactiveEngine(config=_config(initiative_level="silent"), store=store)
        assert not eng.may_speak("critical")

    def test_disabled_blocks_all(self, store):
        eng = ProactiveEngine(config=_config(enabled=False), store=store)
        assert not eng.may_speak("critical")

    def test_speak_publishes_when_allowed(self, store):
        bus = _FakeBus()
        eng = ProactiveEngine(config=_config(initiative_level="high"), store=store, bus=bus)
        assert eng.speak("hello sir", importance="normal") is True
        assert len(bus.published) == 1
        assert bus.published[0].text == "hello sir"
        assert eng.suggested_count == 1

    def test_speak_suppressed_when_blocked(self, store):
        bus = _FakeBus()
        eng = ProactiveEngine(config=_config(initiative_level="low"), store=store, bus=bus)
        assert eng.speak("x", importance="normal") is False
        assert bus.published == []


# ── engine: status ─────────────────────────────────────────────────

def test_status_snapshot(store):
    bus = _FakeBus()
    eng = ProactiveEngine(config=_config(), store=store, bus=bus)
    eng.speak("a", importance="normal")
    eng.record_accept()
    eng.record_dismiss("x")
    st = eng.status()
    assert st["level"] == "high"
    assert st["suggested"] == 1
    assert st["accepted"] == 1
    assert st["dismissed"] == 1


# ── config + migration ─────────────────────────────────────────────

def test_proactive_config_defaults():
    from jarvis.core.config import JarvisConfig

    cfg = JarvisConfig()
    assert cfg.proactive.enabled is True
    assert cfg.proactive.initiative_level == "high"
    assert cfg.proactive.work_hours_threshold == 4.0


def test_migration_v37_to_v38():
    from jarvis.core.migrations import _migrate_v37_to_v38, MIGRATIONS

    data = _migrate_v37_to_v38({"schema_version": 37})
    assert data["schema_version"] == 38
    assert data["proactive"]["initiative_level"] == "high"
    assert 37 in MIGRATIONS
