"""Voice tools for SkillForge — 9 commands to manage user-generated skills.

Management:
  1. list_skills      — "мои скилы" / "my skills" / "мої скіли"
  2. skill_detail     — "расскажи о скиле" / "tell about skill" / "розкажи про скіл"
  3. approve_skill    — "подтверждаю" / "approve" / "підтверджую"
  4. reject_skill     — "отклоняю" / "reject" / "відхиляю"
  5. delete_skill     — "удали скил X" / "delete skill X" / "видали скіл X"
  6. disable_all      — "отключи все скилы" / "disable all skills" (kill switch)

Analysis + editing:
  7. analyze_skills   — "проанализируй скилы" / "analyze skills" / "проаналізуй скіли"
  8. improve_skill    — "улучши скил X" / "improve skill X" / "покращ скіл X"
  9. skill_history    — "история скила X" / "skill history X" / "історія скіла X"

All operate on a shared SkillForge injected at construction (wired in
app.py / setup_local_tools).  With no forge (tests, headless) they degrade
to a friendly message.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ── Args ─────────────────────────────────────────────────────────────────

class EmptySkillArgs(BaseModel):
    pass


class SkillNameArgs(BaseModel):
    name: str = Field(description="Name of the skill.")


class ApproveArgs(BaseModel):
    pass


class RejectArgs(BaseModel):
    reason: str = Field(default="", description="Optional rejection reason.")


class DeleteSkillArgs(BaseModel):
    name: str = Field(description="Name of the skill to delete.")


class ImproveSkillArgs(BaseModel):
    name: str = Field(description="Name of the skill to improve.")


# ── 1. list_skills ───────────────────────────────────────────────────────

class ListSkillsTool:
    """List all user-generated skills."""

    name: str = "list_skills"
    description: str = (
        "List all user-generated skills with their status, version, and usage. "
        "(RU: мои скилы / список скилов — UK: мої скіли / список скілів)"
    )
    args_schema = EmptySkillArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:my\s+skills|list\s+skills|show\s+skills)$",
                     priority=170),
        VoicePattern(regex=r"^(?:мои\s+скилы|список\s+скилов|покажи\s+скилы)$",
                     priority=170),
        VoicePattern(regex=r"^(?:мої\s+скіли|список\s+скілів|покажи\s+скіли)$",
                     priority=170),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: EmptySkillArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        skills = self._forge.list_skills()
        if not skills:
            return ToolResult(success=True, output="No user-generated skills yet, sir.")
        lines = []
        for s in skills:
            status_icon = {
                "approved": "✅", "proposed": "📝", "disabled": "⛔",
                "rejected": "❌", "updating": "🔄",
            }.get(s["status"], "❓")
            lines.append(
                f"  {status_icon} {s['name']} v{s['version']} "
                f"({s['type']}, {s['status']}) — "
                f"used {s['use_count']}×, failed {s['failure_count']}×"
            )
        return ToolResult(
            success=True,
            output=f"Your skills ({len(skills)}):\n" + "\n".join(lines),
        )


# ── 2. skill_detail ──────────────────────────────────────────────────────

class SkillDetailTool:
    """Show details about a specific skill."""

    name: str = "skill_detail"
    description: str = (
        "Show full details about a user-generated skill — code, triggers, stats. "
        "(RU: расскажи о скиле — UK: розкажи про скіл)"
    )
    args_schema = SkillNameArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:tell\s+(?:me\s+)?about\s+skill\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:расскажи\s+о\s+скиле\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:розкажи\s+про\s+скіл\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: SkillNameArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        skill = self._forge.get_skill(args.name)
        if not skill:
            return ToolResult(success=False, error=f"Skill '{args.name}' not found, sir.")
        lines = [
            f"Skill: {skill['name']} (v{skill['version']})",
            f"  Type: {skill['type']}  Status: {skill['status']}",
            f"  Complexity: {skill['complexity']}",
            f"  Description: {skill['description']}",
            f"  Used: {skill['use_count']}×  Failed: {skill['failure_count']}×",
            f"  Created: {skill['created_at']}",
            f"  Last used: {skill['last_used'] or 'never'}",
        ]
        if skill['trigger_patterns']:
            lines.append(f"  Triggers: {', '.join(skill['trigger_patterns'])}")
        return ToolResult(success=True, output="\n".join(lines))


# ── 3. approve_skill ─────────────────────────────────────────────────────

class ApproveSkillTool:
    """Approve a proposed skill."""

    name: str = "approve_skill"
    description: str = (
        "Approve the latest proposed skill. "
        "(RU: подтверждаю — UK: підтверджую)"
    )
    args_schema = ApproveArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:approve|yes\s+create\s+(?:it|skill))$",
                     priority=168),
        VoicePattern(regex=r"^(?:подтверждаю|да\s+создай)$",
                     priority=168),
        VoicePattern(regex=r"^(?:підтверджую|так\s+створи)$",
                     priority=168),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: ApproveArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        # Find the latest proposed skill
        skills = self._forge.list_skills(status="proposed")
        if not skills:
            return ToolResult(
                success=False,
                error="No skills pending approval, sir.",
            )
        latest = skills[-1]
        ok, msg = self._forge.approve_skill(latest["id"])
        return ToolResult(success=ok, output=msg if ok else None, error=msg if not ok else None)


# ── 4. reject_skill ──────────────────────────────────────────────────────

class RejectSkillTool:
    """Reject a proposed skill."""

    name: str = "reject_skill"
    description: str = (
        "Reject the latest proposed skill. "
        "(RU: отклоняю — UK: відхиляю)"
    )
    args_schema = RejectArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:reject|no\s+don'?t\s+create)$",
                     priority=168, args=lambda m: {"reason": ""}),
        VoicePattern(regex=r"^(?:отклоняю|нет\s+не\s+создавай)$",
                     priority=168, args=lambda m: {"reason": ""}),
        VoicePattern(regex=r"^(?:відхиляю|ні\s+не\s+створюй)$",
                     priority=168, args=lambda m: {"reason": ""}),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: RejectArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        skills = self._forge.list_skills(status="proposed")
        if not skills:
            return ToolResult(success=False, error="No skills pending approval, sir.")
        latest = skills[-1]
        ok, msg = self._forge.reject_skill(latest["id"], args.reason)
        return ToolResult(success=ok, output=msg if ok else None, error=msg if not ok else None)


# ── 5. delete_skill ──────────────────────────────────────────────────────

class DeleteSkillTool:
    """Delete a user-generated skill."""

    name: str = "delete_skill"
    description: str = (
        "Delete a user-generated skill by name. "
        "(RU: удали скил X — UK: видали скіл X)"
    )
    args_schema = DeleteSkillArgs
    requires_confirmation: bool = True
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:delete\s+skill\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:удали\s+скил\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:видали\s+скіл\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: DeleteSkillArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        ok, msg = self._forge.delete_skill(args.name)
        return ToolResult(success=ok, output=msg if ok else None, error=msg if not ok else None)


# ── 6. disable_all_skills ────────────────────────────────────────────────

class DisableAllSkillsTool:
    """Kill switch — disable all user-generated skills."""

    name: str = "disable_all_skills"
    description: str = (
        "Emergency kill switch: disable all user-generated skills immediately. "
        "(RU: отключи все скилы — UK: вимкни всі скіли)"
    )
    args_schema = EmptySkillArgs
    requires_confirmation: bool = True
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:disable\s+all\s+skills|kill\s+switch\s+skills)$",
                     priority=168),
        VoicePattern(regex=r"^(?:отключи\s+все\s+скилы|выключи\s+все\s+скилы)$",
                     priority=168),
        VoicePattern(regex=r"^(?:вимкни\s+всі\s+скіли|вимкнути\s+всі\s+скіли)$",
                     priority=168),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: EmptySkillArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        ok, msg = self._forge.disable_all_skills()
        return ToolResult(success=ok, output=msg if ok else None, error=msg if not ok else None)


# ── 7. analyze_skills ────────────────────────────────────────────────────

class AnalyzeSkillsTool:
    """Run manual skill analysis — find patterns, suggest improvements."""

    name: str = "analyze_skills"
    description: str = (
        "Analyze usage patterns and suggest new skills or improvements. "
        "Triggers Phase B manually. "
        "(RU: проанализируй скилы / найди что улучшить — "
        "UK: проаналізуй скіли / знайди що покращити)"
    )
    args_schema = EmptySkillArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:analyze\s+skills|find\s+improvements|skill\s+analysis)$",
                     priority=170),
        VoicePattern(regex=r"^(?:проанализируй\s+скилы|найди\s+что\s+улучшить)$",
                     priority=170),
        VoicePattern(regex=r"^(?:проаналізуй\s+скіли|знайди\s+що\s+покращити)$",
                     priority=170),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: EmptySkillArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        report = self._forge.analyze()
        if not report.summary or report.summary == "No issues found":
            return ToolResult(
                success=True,
                output="Analysis complete. No patterns or issues found, sir.",
            )
        lines = [f"Analysis: {report.summary}"]
        if report.needs_detected:
            lines.append(f"\nPotential new skills:")
            for need in report.needs_detected[:5]:
                lines.append(f"  • {need.name}: {need.description[:80]}...")
        if report.stale_skills:
            lines.append(f"\nStale skills (unused {self._forge.STALE_DAYS}+ days):")
            for name in report.stale_skills[:5]:
                lines.append(f"  • {name}")
        if report.inefficient_skills:
            lines.append(f"\nInefficient skills (>20% failure rate):")
            for name in report.inefficient_skills[:5]:
                lines.append(f"  • {name}")
        return ToolResult(success=True, output="\n".join(lines))


# ── 8. improve_skill ─────────────────────────────────────────────────────

class ImproveSkillTool:
    """Request improvement of a specific skill."""

    name: str = "improve_skill"
    description: str = (
        "Trigger an update/improvement of a specific user-generated skill. "
        "(RU: улучши скил X — UK: покращ скіл X)"
    )
    args_schema = ImproveSkillArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:improve\s+skill\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:улучши\s+скил\s+|обнови\s+скил\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:покращ\s+скіл\s+|онови\s+скіл\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: ImproveSkillArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        skill = self._forge.get_skill(args.name)
        if not skill:
            return ToolResult(success=False, error=f"Skill '{args.name}' not found, sir.")
        if skill["status"] != "approved":
            return ToolResult(
                success=False,
                error=f"Skill '{args.name}' is in state '{skill['status']}', cannot improve.",
            )
        # Mark as updating — actual generation would happen in Phase C
        conn = self._forge._ensure()
        conn.execute(
            "UPDATE skill_forge SET status = 'updating' WHERE skill_name = ?",
            (args.name,),
        )
        conn.commit()
        return ToolResult(
            success=True,
            output=(
                f"Skill '{args.name}' marked for improvement, sir. "
                f"I'll generate an update during the next idle window."
            ),
        )


# ── 9. skill_history ─────────────────────────────────────────────────────

class SkillHistoryTool:
    """Show version history of a skill."""

    name: str = "skill_history"
    description: str = (
        "Show the version history, changes, and usage stats of a skill. "
        "(RU: история скила X — UK: історія скіла X)"
    )
    args_schema = SkillNameArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:skill\s+history\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:история\s+скила\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
        VoicePattern(regex=r"^(?:історія\s+скіла\s+)(.+)$",
                     priority=170, args=lambda m: {"name": m.group(1).strip()}),
    )

    def __init__(self, *, forge=None) -> None:
        self._forge = forge

    async def execute(self, args: SkillNameArgs) -> ToolResult:
        if self._forge is None:
            return ToolResult(success=False, error="SkillForge is not active, sir.")
        skill = self._forge.get_skill(args.name)
        if not skill:
            return ToolResult(success=False, error=f"Skill '{args.name}' not found, sir.")

        history = self._forge.get_skill_history(args.name)
        lines = [
            f"Skill: {skill['name']} (current v{skill['version']})",
            f"  Status: {skill['status']}  Used: {skill['use_count']}×  "
            f"Failed: {skill['failure_count']}×",
        ]
        if history:
            lines.append(f"\nVersion history ({len(history)} entries):")
            for h in history[:10]:
                lines.append(
                    f"  v{h['version']} ({h['changed_at'][:10]}): "
                    f"{h['change_reason'] or 'no reason given'}"
                )
        else:
            lines.append("\nNo version history yet (only v1).")
        return ToolResult(success=True, output="\n".join(lines))
