"""Tests for jarvis.memory.proactive_engine (Layer 1 foundation)."""

from __future__ import annotations

import re

from types import SimpleNamespace

import pytest

from jarvis.memory.proactive_engine import (
    InitiativeLevel,
    ProactiveEngine,
    ProactivePatternStore,
    ProactiveScenarios,
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


# ═══════════════════════════════════════════════════════════════════
# Layer 2: scenarios, cooldowns, foreground tracking, loop
# ═══════════════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta


def _engine(store, **cfg_over):
    bus = _FakeBus()
    eng = ProactiveEngine(config=_config(**cfg_over), store=store, bus=bus)
    return eng, bus


_NOW = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)  # Wed


# ── A. abandoned project ───────────────────────────────────────────

class TestAbandonedProject:
    def test_fires_on_long_leisure_with_stale_project(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.abandoned_project(
            foreground="Dota 2 — Steam",
            leisure_seconds=3 * 3600,
            now=_NOW,
            stale_project={"name": "Jarvis", "days_idle": 5},
        )
        assert r is not None
        assert r[1] == "important"
        assert "Jarvis" in r[0]

    def test_silent_when_not_leisure(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.abandoned_project(
            foreground="main.py — VS Code", leisure_seconds=9 * 3600,
            now=_NOW, stale_project={"name": "X", "days_idle": 9},
        )
        assert r is None

    def test_silent_under_threshold(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.abandoned_project(
            foreground="YouTube", leisure_seconds=600,
            now=_NOW, stale_project={"name": "X", "days_idle": 9},
        )
        assert r is None

    def test_respects_24h_dismiss_snooze(self, store):
        eng, _ = _engine(store)
        eng.record_dismiss("project:Jarvis")  # stamps last_seen=now
        sc = ProactiveScenarios(eng)
        r = sc.abandoned_project(
            foreground="Steam", leisure_seconds=3 * 3600,
            now=datetime.now(tz=timezone.utc),
            stale_project={"name": "Jarvis", "days_idle": 5},
        )
        assert r is None  # snoozed


# ── B. upcoming date ───────────────────────────────────────────────

class TestUpcomingDate:
    def test_fires_within_7_days(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.upcoming_date(
            events=[{"name": "Anna", "date": "Oct 25", "days_until": 4}],
            now=_NOW,
        )
        assert r is not None and "Anna" in r[0] and r[1] == "important"

    def test_today_is_critical(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.upcoming_date(
            events=[{"name": "Bob", "date": "today", "days_until": 0}], now=_NOW,
        )
        assert r is not None and r[1] == "critical"

    def test_picks_soonest(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        r = sc.upcoming_date(
            events=[
                {"name": "Far", "days_until": 6},
                {"name": "Near", "days_until": 1},
            ],
            now=_NOW,
        )
        assert "Near" in r[0]

    def test_ignores_beyond_7_days(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        assert sc.upcoming_date(
            events=[{"name": "X", "days_until": 30}], now=_NOW
        ) is None


# ── C. long work (health) ──────────────────────────────────────────

class TestLongWork:
    def test_fires_after_threshold(self, store):
        eng, _ = _engine(store, work_hours_threshold=4.0)
        sc = ProactiveScenarios(eng)
        r = sc.long_work(foreground="code.py — VS Code", focus_seconds=5 * 3600, now=_NOW)
        assert r is not None and r[1] == "critical" and "5 hours" in r[0]

    def test_silent_for_leisure_app(self, store):
        eng, _ = _engine(store)
        sc = ProactiveScenarios(eng)
        assert sc.long_work(foreground="Netflix", focus_seconds=9 * 3600, now=_NOW) is None

    def test_cooldown_blocks_repeat(self, store):
        eng, _ = _engine(store, work_hours_threshold=4.0)
        sc = ProactiveScenarios(eng)
        first = sc.long_work(foreground="IDE", focus_seconds=5 * 3600, now=_NOW)
        assert first is not None
        again = sc.long_work(
            foreground="IDE", focus_seconds=6 * 3600, now=_NOW + timedelta(minutes=30)
        )
        assert again is None  # within 2h cooldown


# ── D. boot routine ─────────────────────────────────────────────────

class TestBootRoutine:
    def test_fires_on_matching_context(self, store):
        eng, _ = _engine(store, boot_similarity_threshold=0.7)
        boot_now = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)  # Wed
        # Context is derived in LOCAL time (that's the point of "boot time"),
        # so build the expected context the same way to stay tz-robust.
        local_hour = boot_now.astimezone().hour
        ctx = {f"h{local_hour}", "weekday"}
        for _ in range(3):
            eng.record_boot_sequence(["chrome", "spotify", "slack"], now=boot_now)
        sc = ProactiveScenarios(eng)
        r = sc.boot_routine(current_context=ctx)
        assert r is not None and "chrome" in r[0]

    def test_no_fire_below_3_repeats(self, store):
        eng, _ = _engine(store)
        boot_now = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
        ctx = {f"h{boot_now.astimezone().hour}", "weekday"}
        eng.record_boot_sequence(["chrome"], now=boot_now)
        sc = ProactiveScenarios(eng)
        assert sc.boot_routine(current_context=ctx) is None

    def test_no_fire_on_dissimilar_context(self, store):
        eng, _ = _engine(store)
        for _ in range(3):
            eng.record_boot_sequence(["chrome"], now=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc))
        sc = ProactiveScenarios(eng)
        assert sc.boot_routine(current_context={"h22", "weekend"}) is None


# ── E. command sequence ─────────────────────────────────────────────

class TestCommandSequence:
    def test_fires_at_5_plus_high_confidence(self, store):
        eng, _ = _engine(store)
        for _ in range(5):
            eng.record_command_after("git commit", "pytest")
        sc = ProactiveScenarios(eng)
        r = sc.command_sequence(last_command="git commit")
        assert r is not None and "pytest" in r[0]

    def test_no_fire_below_5(self, store):
        eng, _ = _engine(store)
        for _ in range(4):
            eng.record_command_after("git commit", "pytest")
        sc = ProactiveScenarios(eng)
        assert sc.command_sequence(last_command="git commit") is None

    def test_no_fire_low_confidence(self, store):
        eng, _ = _engine(store)
        # pytest 5x but many other follows → confidence < 70%
        for _ in range(5):
            eng.record_command_after("git commit", "pytest")
        for _ in range(6):
            eng.record_command_after("git commit", "push")
        sc = ProactiveScenarios(eng)
        assert sc.command_sequence(last_command="git commit") is None


# ── foreground tracking + cooldown ──────────────────────────────────

class TestForegroundTracking:
    def test_focus_timer_accumulates(self, store):
        eng, _ = _engine(store)
        t0 = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        assert eng.observe_foreground("VS Code", t0) == 0.0
        secs = eng.observe_foreground("VS Code", t0 + timedelta(hours=2))
        assert secs == pytest.approx(7200)

    def test_focus_resets_on_switch(self, store):
        eng, _ = _engine(store)
        t0 = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        eng.observe_foreground("VS Code", t0)
        eng.observe_foreground("VS Code", t0 + timedelta(hours=1))
        assert eng.observe_foreground("Chrome", t0 + timedelta(hours=1)) == 0.0

    def test_cooldown_ok_then_blocked(self, store):
        eng, _ = _engine(store)
        t0 = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        assert eng.cooldown_ok("k", 3600, t0) is True
        assert eng.cooldown_ok("k", 3600, t0 + timedelta(minutes=30)) is False
        assert eng.cooldown_ok("k", 3600, t0 + timedelta(hours=2)) is True


# ── maybe_speak_scenario gate ──────────────────────────────────────

class TestMaybeSpeakScenario:
    def test_speaks_when_level_allows(self, store):
        eng, bus = _engine(store, initiative_level="high")
        spoke = eng.maybe_speak_scenario(("hi", "normal", "k"))
        assert spoke and len(bus.published) == 1

    def test_blocked_by_level(self, store):
        eng, bus = _engine(store, initiative_level="low")
        spoke = eng.maybe_speak_scenario(("hi", "normal", "k"))
        assert not spoke and bus.published == []

    def test_none_result_is_noop(self, store):
        eng, bus = _engine(store)
        assert eng.maybe_speak_scenario(None) is False


# ── background loop ─────────────────────────────────────────────────

async def test_run_loop_ticks_then_stops(store):
    import asyncio
    eng, _ = _engine(store, check_interval_seconds=15)
    ticks = []

    def tick():
        ticks.append(1)
        if len(ticks) >= 2:
            eng.stop()

    # Patch sleep to avoid waiting the real interval.
    async def fast_sleep(_):
        return None

    import jarvis.memory.proactive_engine as mod
    orig = mod.asyncio.sleep
    mod.asyncio.sleep = fast_sleep
    try:
        await asyncio.wait_for(eng.run(tick), timeout=2.0)
    finally:
        mod.asyncio.sleep = orig
    assert len(ticks) >= 2


# ═══════════════════════════════════════════════════════════════════
# Layer 3: voice tools
# ═══════════════════════════════════════════════════════════════════

from jarvis.tools.local.proactive_tools import (
    DismissProactiveArgs,
    DismissProactiveTool,
    EmptyProactiveArgs,
    ProactiveStatusTool,
    SetInitiativeArgs,
    SetInitiativeLevelTool,
)


class TestProactiveTools:
    async def test_set_initiative_up(self, store):
        eng, _ = _engine(store, initiative_level="medium")
        tool = SetInitiativeLevelTool(engine=eng)
        r = await tool.execute(SetInitiativeArgs(direction="up"))
        assert r.success
        assert eng.level.value == "high"

    async def test_set_initiative_down(self, store):
        eng, _ = _engine(store, initiative_level="high")
        tool = SetInitiativeLevelTool(engine=eng)
        r = await tool.execute(SetInitiativeArgs(direction="down"))
        assert r.success
        assert eng.level.value == "medium"

    async def test_set_initiative_silent(self, store):
        eng, _ = _engine(store, initiative_level="high")
        tool = SetInitiativeLevelTool(engine=eng)
        r = await tool.execute(SetInitiativeArgs(direction="silent"))
        assert r.success
        assert eng.level.value == "silent"

    async def test_set_initiative_no_engine(self):
        tool = SetInitiativeLevelTool(engine=None)
        r = await tool.execute(SetInitiativeArgs(direction="up"))
        assert not r.success

    async def test_status_reports(self, store):
        eng, _ = _engine(store, initiative_level="low")
        r = await ProactiveStatusTool(engine=eng).execute(EmptyProactiveArgs())
        assert r.success and "low" in r.output.lower()

    async def test_dismiss_now(self, store):
        eng, _ = _engine(store)
        r = await DismissProactiveTool(engine=eng).execute(DismissProactiveArgs(scope="now"))
        assert r.success
        assert eng.dismissed_count == 1

    async def test_dismiss_forever(self, store):
        eng, _ = _engine(store)
        r = await DismissProactiveTool(engine=eng).execute(
            DismissProactiveArgs(scope="forever")
        )
        assert r.success and "again" in r.output.lower()

    def test_voice_patterns_route(self):
        def match(tool_cls, phrase):
            for vp in sorted(tool_cls.voice_patterns, key=lambda p: p.priority):
                m = re.match(vp.regex, phrase.strip().lower())
                if m:
                    return vp.args(m)
            return None

        assert match(SetInitiativeLevelTool, "будь активнее") == {"direction": "up"}
        assert match(SetInitiativeLevelTool, "не мешай") == {"direction": "down"}
        assert match(SetInitiativeLevelTool, "be quiet") == {"direction": "silent"}
        assert match(DismissProactiveTool, "not now") == {"scope": "now"}
        assert match(DismissProactiveTool, "don't remind me about this") == {"scope": "forever"}
        assert match(ProactiveStatusTool, "какой уровень инициативы") == {}


def test_proactive_config_tab_importable():
    """The Settings tab imports and the config section it reads exists."""
    from jarvis.core.config import JarvisConfig
    cfg = JarvisConfig()
    assert cfg.proactive.initiative_level in {"high", "medium", "low", "silent"}
