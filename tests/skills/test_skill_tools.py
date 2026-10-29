"""Tests for jarvis.tools.local.skill_tools — 9 voice commands.

Tests all 9 tools:
  1. list_skills
  2. skill_detail
  3. approve_skill
  4. reject_skill
  5. delete_skill
  6. disable_all_skills
  7. analyze_skills
  8. improve_skill
  9. skill_history

Plus: voice pattern coverage and wiring in catalogue/risk.
"""

from __future__ import annotations

import pytest

from jarvis.skills.decomposer import SkillDecomposer
from jarvis.skills.sandbox import SkillSandbox
from jarvis.skills.skill_forge import SkillForge
from jarvis.tools.local.skill_tools import (
    AnalyzeSkillsTool,
    ApproveArgs,
    ApproveSkillTool,
    DeleteSkillArgs,
    DeleteSkillTool,
    DisableAllSkillsTool,
    EmptySkillArgs,
    ImproveSkillArgs,
    ImproveSkillTool,
    ListSkillsTool,
    RejectArgs,
    RejectSkillTool,
    SkillDetailTool,
    SkillHistoryTool,
    SkillNameArgs,
)


@pytest.fixture
def forge(tmp_path):
    skills_dir = tmp_path / "user_generated"
    skills_dir.mkdir()
    sf = SkillForge(
        db_path=tmp_path / "test.db",
        sandbox=SkillSandbox(skills_dir),
        decomposer=SkillDecomposer(),
        skills_dir=skills_dir,
    )
    sf.open()
    yield sf
    sf.close()


# ── 1. list_skills ───────────────────────────────────────────────────────

class TestListSkillsTool:
    @pytest.mark.asyncio
    async def test_no_forge(self):
        tool = ListSkillsTool(forge=None)
        r = await tool.execute(EmptySkillArgs())
        assert not r.success
        assert "not active" in r.error

    @pytest.mark.asyncio
    async def test_empty_list(self, forge):
        tool = ListSkillsTool(forge=forge)
        r = await tool.execute(EmptySkillArgs())
        assert r.success
        assert "No user-generated skills" in r.output

    @pytest.mark.asyncio
    async def test_with_skills(self, forge):
        forge.create_skill("s1", "macro", "Test 1", "")
        forge.create_skill("s2", "code", "Test 2", "import json\nx=1")
        tool = ListSkillsTool(forge=forge)
        r = await tool.execute(EmptySkillArgs())
        assert r.success
        assert "s1" in r.output
        assert "s2" in r.output


# ── 2. skill_detail ──────────────────────────────────────────────────────

class TestSkillDetailTool:
    @pytest.mark.asyncio
    async def test_not_found(self, forge):
        tool = SkillDetailTool(forge=forge)
        r = await tool.execute(SkillNameArgs(name="nope"))
        assert not r.success

    @pytest.mark.asyncio
    async def test_found(self, forge):
        forge.create_skill("detail_me", "macro", "Detailed skill", "")
        tool = SkillDetailTool(forge=forge)
        r = await tool.execute(SkillNameArgs(name="detail_me"))
        assert r.success
        assert "detail_me" in r.output


# ── 3. approve_skill ─────────────────────────────────────────────────────

class TestApproveSkillTool:
    @pytest.mark.asyncio
    async def test_no_pending(self, forge):
        tool = ApproveSkillTool(forge=forge)
        r = await tool.execute(ApproveArgs())
        assert not r.success

    @pytest.mark.asyncio
    async def test_approve(self, forge):
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
               VALUES ('proposed', 'pending', 'macro', 'Pending', '',
                       '[]', '', '', 1, 'simple', 1, 0,
                       '2025-01-01', '2025-01-01', '',
                       '', '', '', '', '', '', 0, '', 0, 0)"""
        )
        conn.commit()
        tool = ApproveSkillTool(forge=forge)
        r = await tool.execute(ApproveArgs())
        assert r.success
        assert forge.get_skill("pending")["status"] == "approved"


# ── 4. reject_skill ──────────────────────────────────────────────────────

class TestRejectSkillTool:
    @pytest.mark.asyncio
    async def test_reject(self, forge):
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
               VALUES ('proposed', 'reject_me', 'macro', 'Reject', '',
                       '[]', '', '', 1, 'simple', 1, 0,
                       '2025-01-01', '2025-01-01', '',
                       '', '', '', '', '', '', 0, '', 0, 0)"""
        )
        conn.commit()
        tool = RejectSkillTool(forge=forge)
        r = await tool.execute(RejectArgs(reason="bad idea"))
        assert r.success


# ── 5. delete_skill ──────────────────────────────────────────────────────

class TestDeleteSkillTool:
    @pytest.mark.asyncio
    async def test_delete(self, forge):
        forge.create_skill("delete_me", "macro", "Delete", "")
        tool = DeleteSkillTool(forge=forge)
        r = await tool.execute(DeleteSkillArgs(name="delete_me"))
        assert r.success
        assert forge.get_skill("delete_me") is None


# ── 6. disable_all_skills ────────────────────────────────────────────────

class TestDisableAllSkillsTool:
    @pytest.mark.asyncio
    async def test_kill_switch(self, forge):
        forge.create_skill("s1", "macro", "S1", "")
        forge.create_skill("s2", "macro", "S2", "")
        tool = DisableAllSkillsTool(forge=forge)
        r = await tool.execute(EmptySkillArgs())
        assert r.success
        assert len(forge.list_skills(status="approved")) == 0


# ── 7. analyze_skills ────────────────────────────────────────────────────

class TestAnalyzeSkillsTool:
    @pytest.mark.asyncio
    async def test_empty_analysis(self, forge):
        tool = AnalyzeSkillsTool(forge=forge)
        r = await tool.execute(EmptySkillArgs())
        assert r.success
        assert "no patterns" in r.output.lower() or "no issues" in r.output.lower()


# ── 8. improve_skill ─────────────────────────────────────────────────────

class TestImproveSkillTool:
    @pytest.mark.asyncio
    async def test_not_found(self, forge):
        tool = ImproveSkillTool(forge=forge)
        r = await tool.execute(ImproveSkillArgs(name="nope"))
        assert not r.success

    @pytest.mark.asyncio
    async def test_improve(self, forge):
        forge.create_skill("improve_me", "code", "Improve",
                          "import json\ndef run(): pass")
        tool = ImproveSkillTool(forge=forge)
        r = await tool.execute(ImproveSkillArgs(name="improve_me"))
        assert r.success
        assert forge.get_skill("improve_me")["status"] == "updating"


# ── 9. skill_history ─────────────────────────────────────────────────────

class TestSkillHistoryTool:
    @pytest.mark.asyncio
    async def test_no_history(self, forge):
        forge.create_skill("new", "macro", "New", "")
        tool = SkillHistoryTool(forge=forge)
        r = await tool.execute(SkillNameArgs(name="new"))
        assert r.success
        assert "No version history" in r.output or "only v1" in r.output.lower()

    @pytest.mark.asyncio
    async def test_with_history(self, forge):
        forge.create_skill("versioned", "code", "V",
                          "import json\ndef run(): return 'v1'")
        forge.update_skill("versioned", "import json\ndef run(): return 'v2'",
                          change_reason="fix")
        tool = SkillHistoryTool(forge=forge)
        r = await tool.execute(SkillNameArgs(name="versioned"))
        assert r.success
        assert "fix" in r.output


# ── Voice patterns ───────────────────────────────────────────────────────

class TestVoicePatterns:
    def test_all_tools_have_patterns(self):
        tools = [
            ListSkillsTool, SkillDetailTool, ApproveSkillTool,
            RejectSkillTool, DeleteSkillTool, DisableAllSkillsTool,
            AnalyzeSkillsTool, ImproveSkillTool, SkillHistoryTool,
        ]
        for tool_cls in tools:
            patterns = getattr(tool_cls, "voice_patterns", ())
            assert len(patterns) >= 2, f"{tool_cls.name} needs EN+RU patterns"


# ── Catalogue & Risk registration ────────────────────────────────────────

class TestRegistration:
    def test_in_catalogue(self):
        from jarvis.tools.catalogue import local_tool_names
        names = local_tool_names()
        expected = {
            "list_skills", "skill_detail", "approve_skill", "reject_skill",
            "delete_skill", "disable_all_skills", "analyze_skills",
            "improve_skill", "skill_history",
        }
        for name in expected:
            assert name in names, f"{name} missing from catalogue"

    def test_in_risk_map(self):
        from jarvis.tools.risk import TOOL_RISK_MAP, RiskLevel
        assert TOOL_RISK_MAP["list_skills"] == RiskLevel.READ
        assert TOOL_RISK_MAP["delete_skill"] == RiskLevel.DESTRUCTIVE
        assert TOOL_RISK_MAP["disable_all_skills"] == RiskLevel.DESTRUCTIVE
        assert TOOL_RISK_MAP["approve_skill"] == RiskLevel.WRITE

    def test_setup_local_tools_registers_skills(self):
        """setup_local_tools(skill_forge=None) should register all 9 tools."""
        from jarvis.core.config import ToolsConfig
        from jarvis.tools import ToolRegistry, setup_local_tools
        registry = ToolRegistry(ToolsConfig())
        setup_local_tools(registry, skill_forge=None)
        for name in ["list_skills", "skill_detail", "approve_skill",
                      "reject_skill", "delete_skill", "disable_all_skills",
                      "analyze_skills", "improve_skill", "skill_history"]:
            assert registry.get(name) is not None, f"{name} not registered"
