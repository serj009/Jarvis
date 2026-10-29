"""Tests for jarvis.skills.skill_forge — the main engine.

Covers:
  - SQLite table creation
  - Phase A: observation recording
  - Phase B: analysis (failing tools, sequences, slow ops, stale, inefficient)
  - Phase C: generation gate
  - Phase E: creation, approval, rejection, deletion, enable/disable
  - Versioning: update, rollback
  - Queries: list, get_skill, get_skill_history
  - Kill switch: disable_all_skills
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from jarvis.skills.decomposer import SkillDecomposer, SkillNeed
from jarvis.skills.sandbox import SkillSandbox
from jarvis.skills.skill_forge import SkillForge


@pytest.fixture
def forge(tmp_path):
    """Create a SkillForge with a temporary database."""
    skills_dir = tmp_path / "user_generated"
    skills_dir.mkdir()
    sandbox = SkillSandbox(skills_dir)
    decomposer = SkillDecomposer()
    sf = SkillForge(
        db_path=tmp_path / "test_forge.db",
        sandbox=sandbox,
        decomposer=decomposer,
        skills_dir=skills_dir,
    )
    sf.open()
    yield sf
    sf.close()


# ── Database ─────────────────────────────────────────────────────────────

class TestDatabase:
    def test_tables_created(self, forge):
        conn = forge._ensure()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {t[0] for t in tables}
        assert "skill_observations" in names
        assert "skill_forge" in names
        assert "skill_versions" in names

    def test_double_open_safe(self, forge):
        """Opening twice should not crash or lose data."""
        forge.observe("test_tool")
        forge.close()
        forge.open()
        conn = forge._ensure()
        count = conn.execute("SELECT COUNT(*) FROM skill_observations").fetchone()[0]
        assert count >= 1


# ── Phase A: Observation ─────────────────────────────────────────────────

class TestObservation:
    def test_observe_records(self, forge):
        forge.observe("open_app", {"name": "spotify"}, success=True, duration=0.5)
        conn = forge._ensure()
        rows = conn.execute("SELECT * FROM skill_observations").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "open_app"  # tool_name

    def test_observe_failure(self, forge):
        forge.observe("bad_tool", success=False, error="timeout")
        conn = forge._ensure()
        row = conn.execute("SELECT success, error FROM skill_observations").fetchone()
        assert row[0] == 0
        assert row[1] == "timeout"

    def test_observe_never_raises(self, forge):
        """observe() must never raise, even with bad state."""
        forge.close()  # Close DB
        forge.observe("x")  # Should not raise


# ── Phase B: Analysis ────────────────────────────────────────────────────

class TestAnalysis:
    def test_analyze_empty(self, forge):
        report = forge.analyze()
        assert report.summary == "No issues found"
        assert report.needs_detected == []

    def test_detect_failing_tools(self, forge):
        # Create 6 failures for a tool
        for _ in range(6):
            forge.observe("buggy_tool", success=False, error="fail")
        report = forge.analyze()
        names = [n.name for n in report.needs_detected]
        assert any("buggy_tool" in n for n in names)

    def test_detect_slow_tools(self, forge):
        # Create slow observations
        for _ in range(3):
            forge.observe("slow_tool", success=True, duration=10.0)
        report = forge.analyze()
        names = [n.name for n in report.needs_detected]
        assert any("slow_tool" in n for n in names)

    def test_stale_skills_detected(self, forge):
        conn = forge._ensure()
        conn.execute(
            """INSERT INTO skill_forge
               (status, skill_name, skill_type, description, code,
                trigger_patterns, auto_trigger, test_result, version,
                complexity, parts_total, parts_completed,
                created_at, proposed_at, approved_at,
                rejected_at, rejection_reason,
                updated_at, update_description,
                source_type, source_data,
                use_count, last_used, failure_count, generation_attempts)
               VALUES ('approved', 'old_skill', 'macro', 'old', '',
                       '[]', '', '', 1, 'simple', 1, 1,
                       '2020-01-01', '', '2020-01-01',
                       '', '', '', '', '', '', 0, '2020-01-01', 0, 0)"""
        )
        conn.commit()
        report = forge.analyze()
        assert "old_skill" in report.stale_skills

    def test_inefficient_skills_detected(self, forge):
        conn = forge._ensure()
        conn.execute(
            """INSERT INTO skill_forge
               (status, skill_name, skill_type, description, code,
                trigger_patterns, auto_trigger, test_result, version,
                complexity, parts_total, parts_completed,
                created_at, proposed_at, approved_at,
                rejected_at, rejection_reason,
                updated_at, update_description,
                source_type, source_data,
                use_count, last_used, failure_count, generation_attempts)
               VALUES ('approved', 'flaky_skill', 'code', 'flaky', '',
                       '[]', '', '', 1, 'simple', 1, 1,
                       '2025-01-01', '', '2025-01-01',
                       '', '', '', '', '', '', 20, '2025-12-01', 5, 0)"""
        )
        conn.commit()
        report = forge.analyze()
        assert "flaky_skill" in report.inefficient_skills


# ── Phase C: Generation gate ─────────────────────────────────────────────

class TestGenerationGate:
    def test_can_generate_when_idle(self, forge):
        ok, reason = forge.can_generate(idle_minutes=60)
        assert ok

    def test_cannot_generate_when_busy(self, forge):
        ok, reason = forge.can_generate(idle_minutes=5)
        assert not ok
        assert "idle" in reason.lower()

    def test_daily_limit(self, forge):
        forge._skills_generated_today = 1
        from datetime import datetime, timezone
        forge._today_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        ok, reason = forge.can_generate(idle_minutes=60)
        assert not ok
        assert "already" in reason.lower()


# ── Phase D: Proposal ────────────────────────────────────────────────────

class TestProposal:
    def test_propose_simple_skill(self, forge):
        need = SkillNeed(
            name="test_skill", description="Test", skill_type="macro",
            tools_involved=["open_app"], steps=["open"],
        )
        proposal = forge.propose_skill(need)
        assert proposal["status"] == "proposed"
        assert proposal["complexity"] == "simple"

    def test_propose_aborted_skill(self, forge):
        need = SkillNeed(
            name="mega", description="Use subprocess to hack " * 20,
            skill_type="code",
            tools_involved=[f"t{i}" for i in range(10)],
            steps=[f"s{i}" for i in range(15)],
            has_conditionals=True, has_state=True,
        )
        proposal = forge.propose_skill(need)
        assert proposal["status"] == "aborted"


# ── Phase E: Creation ────────────────────────────────────────────────────

class TestCreation:
    def test_create_macro_skill(self, forge):
        ok, msg = forge.create_skill(
            "my_macro", "macro", "A macro skill", "",
            trigger_patterns=["my macro"],
        )
        assert ok
        skills = forge.list_skills()
        assert any(s["name"] == "my_macro" for s in skills)

    def test_create_code_skill_with_safe_code(self, forge):
        code = "import json\ndef run(): return json.dumps({'ok': True})"
        ok, msg = forge.create_skill("safe_code", "code", "Safe code", code)
        assert ok
        # Check file was written
        skill_file = forge._skills_dir / "safe_code.py"
        assert skill_file.exists()

    def test_create_code_skill_with_unsafe_code_rejected(self, forge):
        code = "import os\nos.system('rm -rf /')"
        ok, msg = forge.create_skill("bad_code", "code", "Bad", code)
        assert not ok
        assert "validation failed" in msg.lower()

    def test_approve_proposed_skill(self, forge):
        conn = forge._ensure()
        conn.execute(
            """INSERT INTO skill_forge
               (status, skill_name, skill_type, description, code,
                trigger_patterns, auto_trigger, test_result, version,
                complexity, parts_total, parts_completed,
                created_at, proposed_at, approved_at,
                rejected_at, rejection_reason,
                updated_at, update_description,
                source_type, source_data,
                use_count, last_used, failure_count, generation_attempts)
               VALUES ('proposed', 'new_skill', 'macro', 'New', '',
                       '[]', '', '', 1, 'simple', 1, 0,
                       '2025-01-01', '2025-01-01', '',
                       '', '', '', '', '', '', 0, '', 0, 0)"""
        )
        conn.commit()
        ok, msg = forge.approve_skill(1)
        assert ok
        skill = forge.get_skill("new_skill")
        assert skill["status"] == "approved"

    def test_reject_proposed_skill(self, forge):
        conn = forge._ensure()
        conn.execute(
            """INSERT INTO skill_forge
               (status, skill_name, skill_type, description, code,
                trigger_patterns, auto_trigger, test_result, version,
                complexity, parts_total, parts_completed,
                created_at, proposed_at, approved_at,
                rejected_at, rejection_reason,
                updated_at, update_description,
                source_type, source_data,
                use_count, last_used, failure_count, generation_attempts)
               VALUES ('proposed', 'bad_skill', 'macro', 'Bad', '',
                       '[]', '', '', 1, 'simple', 1, 0,
                       '2025-01-01', '2025-01-01', '',
                       '', '', '', '', '', '', 0, '', 0, 0)"""
        )
        conn.commit()
        ok, msg = forge.reject_skill(1, "Not useful")
        assert ok
        skill = forge.get_skill("bad_skill")
        assert skill["status"] == "rejected"

    def test_delete_skill(self, forge):
        forge.create_skill("to_delete", "macro", "Will be deleted", "")
        ok, msg = forge.delete_skill("to_delete")
        assert ok
        assert forge.get_skill("to_delete") is None

    def test_disable_enable_skill(self, forge):
        forge.create_skill("toggle_me", "macro", "Toggleable", "")
        ok, _ = forge.disable_skill("toggle_me")
        assert ok
        assert forge.get_skill("toggle_me")["status"] == "disabled"
        ok, _ = forge.enable_skill("toggle_me")
        assert ok
        assert forge.get_skill("toggle_me")["status"] == "approved"

    def test_disable_all_skills(self, forge):
        forge.create_skill("s1", "macro", "S1", "")
        forge.create_skill("s2", "macro", "S2", "")
        ok, msg = forge.disable_all_skills()
        assert ok
        skills = forge.list_skills(status="approved")
        assert len(skills) == 0


# ── Versioning ───────────────────────────────────────────────────────────

class TestVersioning:
    def test_update_skill(self, forge):
        code_v1 = "import json\ndef run(): return 'v1'"
        forge.create_skill("versioned", "code", "Versioned", code_v1)
        code_v2 = "import json\ndef run(): return 'v2'"
        ok, msg = forge.update_skill("versioned", code_v2, change_reason="improvement")
        assert ok
        skill = forge.get_skill("versioned")
        assert skill["version"] == 2
        assert "v2" in skill["code"]

    def test_update_with_unsafe_code_rejected(self, forge):
        forge.create_skill("safe", "code", "Safe",
                          "import json\ndef run(): return 1")
        ok, msg = forge.update_skill("safe", "import os\nos.system('bad')")
        assert not ok

    def test_rollback_skill(self, forge):
        code_v1 = "import json\ndef run(): return 'v1'"
        forge.create_skill("rollback_me", "code", "Rollback", code_v1)
        code_v2 = "import json\ndef run(): return 'v2'"
        forge.update_skill("rollback_me", code_v2, change_reason="test")
        ok, msg = forge.rollback_skill("rollback_me")
        assert ok
        skill = forge.get_skill("rollback_me")
        assert "v1" in skill["code"]

    def test_rollback_no_history(self, forge):
        forge.create_skill("fresh", "code", "Fresh",
                          "import json\ndef run(): return 1")
        ok, msg = forge.rollback_skill("fresh")
        assert not ok
        assert "no previous version" in msg.lower()

    def test_skill_history(self, forge):
        forge.create_skill("hist", "code", "History",
                          "import json\ndef run(): return 'v1'")
        forge.update_skill("hist", "import json\ndef run(): return 'v2'",
                          change_reason="fix1")
        forge.update_skill("hist", "import json\ndef run(): return 'v3'",
                          change_reason="fix2")
        history = forge.get_skill_history("hist")
        assert len(history) == 2
        assert history[0]["change_reason"] == "fix2"


# ── Queries ──────────────────────────────────────────────────────────────

class TestQueries:
    def test_list_empty(self, forge):
        skills = forge.list_skills()
        assert skills == []

    def test_list_with_filter(self, forge):
        forge.create_skill("s1", "macro", "S1", "")
        forge.disable_skill("s1")
        forge.create_skill("s2", "macro", "S2", "")
        approved = forge.list_skills(status="approved")
        disabled = forge.list_skills(status="disabled")
        assert len(approved) == 1
        assert len(disabled) == 1

    def test_get_skill_not_found(self, forge):
        assert forge.get_skill("nonexistent") is None

    def test_record_skill_use(self, forge):
        forge.create_skill("used", "macro", "Used", "")
        forge.record_skill_use("used", success=True)
        forge.record_skill_use("used", success=False)
        skill = forge.get_skill("used")
        assert skill["use_count"] == 2
        assert skill["failure_count"] == 1
