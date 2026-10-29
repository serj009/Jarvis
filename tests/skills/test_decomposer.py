"""Tests for jarvis.skills.decomposer — complexity estimation & decomposition."""

from __future__ import annotations


from jarvis.skills.decomposer import (
    ComplexityLevel,
    SkillDecomposer,
    SkillNeed,
    SkillPart,
)


class TestSkillDecomposer:
    def setup_method(self):
        self.d = SkillDecomposer()

    # ── estimate_complexity ──────────────────────────────────────────────

    def test_simple_skill(self):
        need = SkillNeed(
            name="open_spotify",
            description="Open Spotify",
            skill_type="macro",
            tools_involved=["open_app"],
            steps=["open Spotify"],
        )
        c = self.d.estimate_complexity(need)
        assert c.level == ComplexityLevel.SIMPLE
        assert c.estimated_parts == 1
        assert c.estimated_tokens > 0

    def test_medium_skill(self):
        need = SkillNeed(
            name="morning_routine",
            description="Open email, check calendar, start music, weather",
            skill_type="macro",
            tools_involved=["open_url", "open_app", "play_youtube_music", "get_weather"],
            steps=["open email", "check calendar", "start music", "get weather"],
            has_conditionals=True,
        )
        c = self.d.estimate_complexity(need)
        assert c.level in (ComplexityLevel.MEDIUM, ComplexityLevel.COMPLEX)
        assert c.estimated_parts >= 2

    def test_complex_skill(self):
        need = SkillNeed(
            name="auto_backup",
            description="Complex multi-step backup with conditions and state",
            skill_type="code",
            tools_involved=["file_manager", "archive", "download_file", "run_command", "git_tool"],
            steps=["scan dir", "filter by date", "archive", "upload", "verify", "log"],
            has_conditionals=True,
            has_state=True,
        )
        c = self.d.estimate_complexity(need)
        assert c.level == ComplexityLevel.COMPLEX
        assert c.estimated_parts >= 3

    def test_single_tool_is_simple(self):
        need = SkillNeed(
            name="quick_timer",
            description="Set 5 min timer",
            skill_type="macro",
            tools_involved=["set_timer"],
            steps=["set timer"],
        )
        c = self.d.estimate_complexity(need)
        assert c.level == ComplexityLevel.SIMPLE

    # ── decompose ────────────────────────────────────────────────────────

    def test_decompose_simple_returns_one_part(self):
        need = SkillNeed(
            name="simple_skill",
            description="Do one thing",
            skill_type="macro",
            tools_involved=["open_app"],
            steps=["open app"],
        )
        parts = self.d.decompose(need)
        assert len(parts) == 1
        assert parts[0].index == 0
        assert parts[0].total == 1

    def test_decompose_medium_returns_2_3_parts(self):
        need = SkillNeed(
            name="med_skill",
            description="Multi-step with conditions for morning routine setup",
            skill_type="macro",
            tools_involved=["open_url", "open_app", "play_youtube_music"],
            steps=["step1", "step2", "step3"],
            has_conditionals=True,
        )
        parts = self.d.decompose(need)
        assert 2 <= len(parts) <= 3
        assert parts[0].name.endswith("_core")
        assert parts[1].name.endswith("_errors")
        assert parts[1].depends_on == [0]

    def test_decompose_complex_has_orchestrator(self):
        need = SkillNeed(
            name="complex_skill",
            description="Very complex multi-tool multi-step system",
            skill_type="code",
            tools_involved=["t1", "t2", "t3", "t4", "t5"],
            steps=["s1", "s2", "s3", "s4", "s5", "s6"],
            has_conditionals=True,
            has_state=True,
        )
        parts = self.d.decompose(need)
        assert len(parts) >= 3
        # Last part should be the orchestrator
        last = parts[-1]
        assert last.name == "complex_skill"
        assert "orchestrator" in last.description.lower() or "macro" in last.focus.lower()

    # ── should_abort ─────────────────────────────────────────────────────

    def test_should_abort_too_complex(self):
        """Skill needing >5 parts should be aborted."""
        need = SkillNeed(
            name="mega_skill",
            description="An incredibly complex system with many tools and steps " * 20,
            skill_type="code",
            tools_involved=[f"tool_{i}" for i in range(10)],
            steps=[f"step_{i}" for i in range(15)],
            has_conditionals=True,
            has_state=True,
        )
        abort, reason = self.d.should_abort(need)
        assert abort
        assert "too complex" in reason.lower() or "parts" in reason.lower()

    def test_should_not_abort_simple(self):
        need = SkillNeed(
            name="easy",
            description="Simple one-tool skill",
            skill_type="macro",
            tools_involved=["open_app"],
            steps=["open"],
        )
        abort, reason = self.d.should_abort(need)
        assert not abort
        assert reason == ""

    def test_should_abort_blocked_module(self):
        """Skill description mentioning blocked modules should abort."""
        need = SkillNeed(
            name="net_skill",
            description="Use subprocess to run network commands",
            skill_type="code",
            tools_involved=["run_command"],
            steps=["run subprocess"],
        )
        abort, reason = self.d.should_abort(need)
        assert abort
        assert "subprocess" in reason

    def test_skill_part_dataclass(self):
        p = SkillPart(index=0, total=3, name="p1", description="d", focus="f")
        assert p.index == 0
        assert p.total == 3
        assert p.depends_on == []

    def test_skill_need_defaults(self):
        n = SkillNeed(name="n", description="d", skill_type="macro")
        assert n.tools_involved == []
        assert n.steps == []
        assert not n.has_conditionals
        assert not n.has_state
