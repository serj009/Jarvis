"""SkillForge — the engine that observes usage and generates skills.

Phases:
    A — Observation (0 VRAM, on every command): INSERT into skill_observations
    B — Analysis (0 VRAM, scheduled or manual): find patterns, detect needs
    C — Generation (LLM, strict limits): max 1/day, idle > 30 min, etc.
    D — Proposal (ProactiveSpeech + UI): present to user with preview
    E — Creation + Editing: save, version, rollback

SQLite tables:
    skill_observations — raw tool-call telemetry
    skill_forge        — skill records (detected → approved lifecycle)
    skill_versions     — version history for rollback

Integration:
    - Reads ``UsageStats`` (jarvis.memory.usage_stats)
    - Pushes via ``ProactiveSpeech`` event
    - Creates files in ``jarvis/skills/user_generated/``
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.skills.decomposer import (
    ComplexityLevel,
    SkillComplexity,
    SkillDecomposer,
    SkillNeed,
    SkillPart,
)
from jarvis.skills.sandbox import SkillSandbox, SkillValidator

log = logging.getLogger(__name__)


def _default_db_path() -> Path:
    # Resolved at call time via the single data-dir resolver (jarvis.paths);
    # layout unchanged: <data_dir>/skill_forge.db.
    from jarvis.paths import data_path

    return data_path("skill_forge.db")


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class SkillRecord:
    """A skill record from the ``skill_forge`` table."""
    id: int
    status: str
    skill_name: str
    skill_type: str
    description: str
    code: str
    trigger_patterns: str
    auto_trigger: str
    test_result: str
    version: int
    parent_skill_id: int | None
    complexity: str
    parts_total: int
    parts_completed: int
    created_at: str
    proposed_at: str
    approved_at: str
    rejected_at: str
    rejection_reason: str
    updated_at: str
    update_description: str
    source_type: str
    source_data: str
    use_count: int
    last_used: str
    failure_count: int
    generation_attempts: int


@dataclass
class SkillObservation:
    """A single tool-call observation."""
    tool_name: str
    args: dict
    success: bool
    confidence: float
    duration: float
    error: str
    context: str


@dataclass
class AnalysisReport:
    """Result of a Phase B analysis."""
    needs_detected: list[SkillNeed] = field(default_factory=list)
    stale_skills: list[str] = field(default_factory=list)
    inefficient_skills: list[str] = field(default_factory=list)
    update_candidates: list[str] = field(default_factory=list)
    summary: str = ""


# ── Skill Forge ──────────────────────────────────────────────────────────

class SkillForge:
    """The core engine: observe → analyze → propose → create → manage skills.

    Parameters:
        db_path:     SQLite database path (default ~/.jarvis/skill_forge.db).
        sandbox:     The SkillSandbox for validation/execution.
        decomposer:  For estimating complexity and splitting large skills.
        skills_dir:  Directory for user_generated skill files.
    """

    # Analysis thresholds (Phase B)
    TOOL_FAIL_THRESHOLD: int = 5           # fails in 7 days
    LOW_CONFIDENCE_THRESHOLD: float = 0.7
    SEQUENCE_MIN_LENGTH: int = 3           # min commands in repeating pattern
    SEQUENCE_MIN_REPEATS: int = 5          # min times pattern must repeat
    SLOW_OPERATION_THRESHOLD: float = 5.0  # seconds
    STALE_DAYS: int = 30                   # unused days before "stale"
    INEFFICIENCY_THRESHOLD: float = 0.2    # failure_rate > 20%
    INEFFICIENCY_MIN_USES: int = 10        # min uses before checking rate

    # Generation limits (Phase C)
    MAX_SKILLS_PER_DAY: int = 1
    IDLE_REQUIRED_MINUTES: int = 30
    ANALYSIS_INTERVAL_HOURS: int = 4
    TOKEN_BUDGET: int = 2000

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        sandbox: SkillSandbox | None = None,
        decomposer: SkillDecomposer | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        self._db_path = db_path if db_path is not None else _default_db_path()
        self._skills_dir = skills_dir or (
            Path(__file__).resolve().parent / "user_generated"
        )
        self._sandbox = sandbox or SkillSandbox(self._skills_dir)
        self._decomposer = decomposer or SkillDecomposer()
        self._conn: sqlite3.Connection | None = None
        self._last_analysis_time: float = 0.0
        self._skills_generated_today: int = 0
        self._today_date: str = ""

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def sandbox(self) -> SkillSandbox:
        return self._sandbox

    @property
    def decomposer(self) -> SkillDecomposer:
        return self._decomposer

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the SQLite database and create tables if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        log.info("[skill-forge] opened database at %s", self._db_path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SkillForge not open — call open() first")
        return self._conn

    def _create_tables(self) -> None:
        conn = self._ensure()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS skill_observations (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                tool_name TEXT,
                args TEXT,
                success INTEGER,
                confidence REAL,
                duration REAL,
                error TEXT,
                context TEXT
            );

            CREATE TABLE IF NOT EXISTS skill_forge (
                id INTEGER PRIMARY KEY,
                status TEXT,
                skill_name TEXT,
                skill_type TEXT,
                description TEXT,
                code TEXT,
                trigger_patterns TEXT,
                auto_trigger TEXT,
                test_result TEXT,
                version INTEGER DEFAULT 1,
                parent_skill_id INTEGER,
                complexity TEXT,
                parts_total INTEGER DEFAULT 1,
                parts_completed INTEGER DEFAULT 0,
                created_at TEXT,
                proposed_at TEXT,
                approved_at TEXT,
                rejected_at TEXT,
                rejection_reason TEXT,
                updated_at TEXT,
                update_description TEXT,
                source_type TEXT,
                source_data TEXT,
                use_count INTEGER DEFAULT 0,
                last_used TEXT,
                failure_count INTEGER DEFAULT 0,
                generation_attempts INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS skill_versions (
                id INTEGER PRIMARY KEY,
                skill_id INTEGER,
                version INTEGER,
                code TEXT,
                description TEXT,
                changed_at TEXT,
                change_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_observations_tool
                ON skill_observations(tool_name);
            CREATE INDEX IF NOT EXISTS idx_observations_ts
                ON skill_observations(timestamp);
            CREATE INDEX IF NOT EXISTS idx_forge_status
                ON skill_forge(status);
            CREATE INDEX IF NOT EXISTS idx_forge_name
                ON skill_forge(skill_name);
        """)
        conn.commit()

    # ── Phase A: Observation ─────────────────────────────────────────────

    def observe(
        self,
        tool_name: str,
        args: dict | None = None,
        *,
        success: bool = True,
        confidence: float = 1.0,
        duration: float = 0.0,
        error: str = "",
        context: str = "",
    ) -> None:
        """Record a tool call observation.  Zero VRAM, runs on every command.

        Safe to call from any context — never raises.
        """
        try:
            conn = self._ensure()
            now = datetime.now(tz=timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO skill_observations
                   (timestamp, tool_name, args, success, confidence,
                    duration, error, context)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now, tool_name,
                    json.dumps(args or {}),
                    1 if success else 0,
                    confidence, duration, error, context,
                ),
            )
            conn.commit()
        except Exception:
            log.debug("[skill-forge] observe() failed (non-fatal)", exc_info=True)

    # ── Phase B: Analysis ────────────────────────────────────────────────

    def analyze(self) -> AnalysisReport:
        """Run pattern analysis on observations.  Zero VRAM.

        Looks for:
          - Repeating failures (tool fails > N times in 7 days)
          - Low confidence categories (< 70%)
          - Repeating command sequences (3+ commands, 5+ repeats)
          - Time patterns (same thing at same time)
          - Frequent re-asks (> 3 times)
          - Slow operations (> 5s regularly)
          - Stale skills (not used 30+ days)
          - Inefficient skills (use_count > 10 but failure_rate > 20%)

        Returns an :class:`AnalysisReport`.
        """
        conn = self._ensure()
        report = AnalysisReport()
        now = datetime.now(tz=timezone.utc)

        # --- Repeating failures ---
        failing_tools = self._find_failing_tools(conn)
        for tool_name, fail_count in failing_tools:
            report.needs_detected.append(SkillNeed(
                name=f"fix_{tool_name}",
                description=(
                    f"Tool '{tool_name}' failed {fail_count} times in the "
                    f"last 7 days. Consider a wrapper skill with better "
                    f"error handling."
                ),
                skill_type="code",
                tools_involved=[tool_name],
                source_type="error",
                source_data=f"failures={fail_count}",
            ))

        # --- Repeating sequences ---
        sequences = self._find_repeating_sequences(conn)
        for seq_key, count, tools in sequences:
            report.needs_detected.append(SkillNeed(
                name=f"auto_{seq_key}",
                description=(
                    f"Detected repeating sequence ({count} times): "
                    f"{' → '.join(tools)}. Could be automated."
                ),
                skill_type="macro",
                tools_involved=list(tools),
                steps=list(tools),
                source_type="pattern",
                source_data=f"repeats={count}",
            ))

        # --- Slow operations ---
        slow_tools = self._find_slow_operations(conn)
        for tool_name, avg_duration in slow_tools:
            report.needs_detected.append(SkillNeed(
                name=f"optimize_{tool_name}",
                description=(
                    f"Tool '{tool_name}' averages {avg_duration:.1f}s. "
                    f"A caching/pre-fetch skill could improve this."
                ),
                skill_type="code",
                tools_involved=[tool_name],
                source_type="pattern",
                source_data=f"avg_duration={avg_duration:.1f}",
            ))

        # --- Stale skills ---
        stale = self._find_stale_skills(conn)
        report.stale_skills = stale

        # --- Inefficient skills ---
        inefficient = self._find_inefficient_skills(conn)
        report.inefficient_skills = inefficient
        report.update_candidates = inefficient[:]

        # --- Summary ---
        parts = []
        if report.needs_detected:
            parts.append(f"{len(report.needs_detected)} potential new skills")
        if report.stale_skills:
            parts.append(f"{len(report.stale_skills)} stale skills")
        if report.inefficient_skills:
            parts.append(f"{len(report.inefficient_skills)} inefficient skills")
        report.summary = "; ".join(parts) if parts else "No issues found"

        self._last_analysis_time = time.monotonic()
        return report

    def _find_failing_tools(self, conn: sqlite3.Connection) -> list[tuple[str, int]]:
        rows = conn.execute(
            """SELECT tool_name, COUNT(*) as cnt
               FROM skill_observations
               WHERE success = 0
                 AND timestamp >= datetime('now', '-7 days')
               GROUP BY tool_name
               HAVING cnt >= ?""",
            (self.TOOL_FAIL_THRESHOLD,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _find_repeating_sequences(
        self, conn: sqlite3.Connection,
    ) -> list[tuple[str, int, tuple[str, ...]]]:
        """Find repeating tool-call sequences of length >= SEQUENCE_MIN_LENGTH."""
        rows = conn.execute(
            """SELECT tool_name FROM skill_observations
               WHERE timestamp >= datetime('now', '-7 days')
               ORDER BY timestamp""",
        ).fetchall()
        tools = [r[0] for r in rows]

        if len(tools) < self.SEQUENCE_MIN_LENGTH:
            return []

        # Sliding window to find repeated subsequences
        seq_counts: dict[tuple[str, ...], int] = {}
        for window in range(self.SEQUENCE_MIN_LENGTH, min(8, len(tools) // 2 + 1)):
            for i in range(len(tools) - window + 1):
                seq = tuple(tools[i:i + window])
                seq_counts[seq] = seq_counts.get(seq, 0) + 1

        results = []
        seen_tools: set[tuple[str, ...]] = set()
        for seq, count in sorted(seq_counts.items(), key=lambda x: -x[1]):
            if count >= self.SEQUENCE_MIN_REPEATS and seq not in seen_tools:
                key = "_".join(seq)[:40]
                results.append((key, count, seq))
                seen_tools.add(seq)
                if len(results) >= 5:
                    break

        return results

    def _find_slow_operations(self, conn: sqlite3.Connection) -> list[tuple[str, float]]:
        rows = conn.execute(
            """SELECT tool_name, AVG(duration) as avg_dur
               FROM skill_observations
               WHERE success = 1
                 AND timestamp >= datetime('now', '-7 days')
               GROUP BY tool_name
               HAVING avg_dur >= ?""",
            (self.SLOW_OPERATION_THRESHOLD,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _find_stale_skills(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            """SELECT skill_name FROM skill_forge
               WHERE status = 'approved'
                 AND (last_used IS NULL
                      OR last_used <= datetime('now', ?))""",
            (f"-{self.STALE_DAYS} days",),
        ).fetchall()
        return [r[0] for r in rows]

    def _find_inefficient_skills(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            """SELECT skill_name, use_count, failure_count FROM skill_forge
               WHERE status = 'approved'
                 AND use_count >= ?""",
            (self.INEFFICIENCY_MIN_USES,),
        ).fetchall()
        return [
            r[0] for r in rows
            if r[1] > 0 and (r[2] / r[1]) > self.INEFFICIENCY_THRESHOLD
        ]

    # ── Phase C: Generation gate ─────────────────────────────────────────

    def can_generate(self, *, idle_minutes: float = 0.0) -> tuple[bool, str]:
        """Check whether generation is allowed right now.

        Conditions:
          - Max N skills/day not exceeded
          - Idle time >= IDLE_REQUIRED_MINUTES
          - Not in Game Mode (caller checks externally)

        Returns (allowed, reason_if_not).
        """
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_date:
            self._today_date = today
            self._skills_generated_today = 0

        if self._skills_generated_today >= self.MAX_SKILLS_PER_DAY:
            return False, f"Already generated {self._skills_generated_today} skills today"
        if idle_minutes < self.IDLE_REQUIRED_MINUTES:
            return False, f"Need {self.IDLE_REQUIRED_MINUTES} min idle (have {idle_minutes:.0f})"
        return True, ""

    def propose_skill(self, need: SkillNeed) -> dict:
        """Prepare a skill proposal for user review (Phase D).

        Returns a dict with name, description, steps/code, triggers,
        complexity assessment, and estimated timeline.
        """
        complexity = self._decomposer.estimate_complexity(need)
        should_abort, abort_reason = self._decomposer.should_abort(need)

        if should_abort:
            return {
                "status": "aborted",
                "reason": abort_reason,
                "skill_name": need.name,
            }

        parts = self._decomposer.decompose(need)
        timeline = ""
        if complexity.level == ComplexityLevel.SIMPLE:
            timeline = "Immediate (1 generation)"
        elif complexity.level == ComplexityLevel.MEDIUM:
            timeline = f"~{complexity.estimated_parts} days (one part per day)"
        else:
            timeline = f"~{complexity.estimated_parts} days (separate sub-skills)"

        return {
            "status": "proposed",
            "skill_name": need.name,
            "description": need.description,
            "skill_type": need.skill_type,
            "tools_involved": need.tools_involved,
            "steps": need.steps,
            "trigger_patterns": need.trigger_patterns,
            "complexity": complexity.level.value,
            "estimated_tokens": complexity.estimated_tokens,
            "parts": [
                {"name": p.name, "focus": p.focus, "depends_on": p.depends_on}
                for p in parts
            ],
            "timeline": timeline,
        }

    # ── Phase E: Creation ────────────────────────────────────────────────

    def create_skill(
        self,
        name: str,
        skill_type: str,
        description: str,
        code: str,
        *,
        trigger_patterns: list[str] | None = None,
        auto_trigger: str = "",
        source_type: str = "",
        source_data: str = "",
        complexity: str = "simple",
    ) -> tuple[bool, str]:
        """Create a new skill after validation.

        Validates code via AST (Level 3), saves to DB and file system.
        Returns ``(success, message)``.
        """
        conn = self._ensure()

        # AST validation for code skills
        if skill_type == "code" and code.strip():
            is_safe, violations = self._sandbox.validate_code(code)
            if not is_safe:
                msgs = [f"  Line {v.line}: {v.message}" for v in violations]
                return False, "Code validation failed:\n" + "\n".join(msgs)

        now = datetime.now(tz=timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO skill_forge
               (status, skill_name, skill_type, description, code,
                trigger_patterns, auto_trigger, test_result,
                version, parent_skill_id, complexity,
                parts_total, parts_completed,
                created_at, proposed_at, approved_at,
                rejected_at, rejection_reason,
                updated_at, update_description,
                source_type, source_data,
                use_count, last_used, failure_count,
                generation_attempts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, 1, 1,
                       ?, ?, ?, '', '', ?, '', ?, ?, 0, '', 0, 1)""",
            (
                "approved", name, skill_type, description, code,
                json.dumps(trigger_patterns or []),
                auto_trigger, "",
                complexity,
                now, now, now, now,
                source_type, source_data,
            ),
        )
        conn.commit()

        # Write code skill to file
        if skill_type == "code" and code.strip():
            self._write_skill_file(name, code)

        self._skills_generated_today += 1
        log.info("[skill-forge] created skill '%s' (type=%s)", name, skill_type)
        return True, f"Skill '{name}' created successfully."

    def approve_skill(self, skill_id: int) -> tuple[bool, str]:
        """Approve a proposed skill.  Returns (success, message)."""
        conn = self._ensure()
        now = datetime.now(tz=timezone.utc).isoformat()
        row = conn.execute(
            "SELECT status, skill_name, code, skill_type FROM skill_forge WHERE id = ?",
            (skill_id,),
        ).fetchone()
        if not row:
            return False, f"Skill #{skill_id} not found."
        if row[0] not in ("proposed", "designing", "designed"):
            return False, f"Skill #{skill_id} is in state '{row[0]}', cannot approve."

        conn.execute(
            "UPDATE skill_forge SET status = 'approved', approved_at = ? WHERE id = ?",
            (now, skill_id),
        )
        conn.commit()

        # Write file if code skill
        if row[3] == "code" and row[2]:
            self._write_skill_file(row[1], row[2])

        return True, f"Skill '{row[1]}' approved."

    def reject_skill(self, skill_id: int, reason: str = "") -> tuple[bool, str]:
        """Reject a proposed skill.  Returns (success, message)."""
        conn = self._ensure()
        now = datetime.now(tz=timezone.utc).isoformat()
        row = conn.execute(
            "SELECT status, skill_name FROM skill_forge WHERE id = ?",
            (skill_id,),
        ).fetchone()
        if not row:
            return False, f"Skill #{skill_id} not found."

        conn.execute(
            """UPDATE skill_forge
               SET status = 'rejected', rejected_at = ?, rejection_reason = ?
               WHERE id = ?""",
            (now, reason, skill_id),
        )
        conn.commit()
        return True, f"Skill '{row[1]}' rejected."

    def delete_skill(self, skill_name: str) -> tuple[bool, str]:
        """Delete a skill by name.  Removes DB record and file."""
        conn = self._ensure()
        row = conn.execute(
            "SELECT id, skill_type, code FROM skill_forge WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return False, f"Skill '{skill_name}' not found."

        conn.execute("DELETE FROM skill_forge WHERE id = ?", (row[0],))
        conn.execute("DELETE FROM skill_versions WHERE skill_id = ?", (row[0],))
        conn.commit()

        # Remove file
        self._remove_skill_file(skill_name)
        return True, f"Skill '{skill_name}' deleted."

    def disable_skill(self, skill_name: str) -> tuple[bool, str]:
        """Disable a skill (set status='disabled')."""
        conn = self._ensure()
        row = conn.execute(
            "SELECT id FROM skill_forge WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return False, f"Skill '{skill_name}' not found."

        conn.execute(
            "UPDATE skill_forge SET status = 'disabled' WHERE id = ?",
            (row[0],),
        )
        conn.commit()
        return True, f"Skill '{skill_name}' disabled."

    def enable_skill(self, skill_name: str) -> tuple[bool, str]:
        """Re-enable a disabled skill."""
        conn = self._ensure()
        row = conn.execute(
            "SELECT id, status FROM skill_forge WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return False, f"Skill '{skill_name}' not found."
        if row[1] != "disabled":
            return False, f"Skill '{skill_name}' is not disabled (status={row[1]})."

        conn.execute(
            "UPDATE skill_forge SET status = 'approved' WHERE id = ?",
            (row[0],),
        )
        conn.commit()
        self._sandbox.circuit_breaker.reset(skill_name)
        return True, f"Skill '{skill_name}' re-enabled."

    def disable_all_skills(self) -> tuple[bool, str]:
        """Kill switch — disable ALL user skills."""
        conn = self._ensure()
        conn.execute(
            "UPDATE skill_forge SET status = 'disabled' WHERE status = 'approved'",
        )
        conn.commit()
        self._sandbox.circuit_breaker.reset_all()
        return True, "All user skills disabled."

    # ── Phase E: Editing / Versioning ────────────────────────────────────

    def update_skill(
        self,
        skill_name: str,
        new_code: str,
        *,
        change_reason: str = "",
    ) -> tuple[bool, str]:
        """Update a skill's code with version tracking + rollback support.

        Process:
          1. status → "updating"
          2. Save current version to skill_versions (for rollback)
          3. AST validate new code
          4. Update skill_forge record, version += 1
          5. Write new file

        Returns (success, message).
        """
        conn = self._ensure()
        row = conn.execute(
            """SELECT id, code, description, version, skill_type
               FROM skill_forge WHERE skill_name = ?""",
            (skill_name,),
        ).fetchone()
        if not row:
            return False, f"Skill '{skill_name}' not found."

        skill_id, old_code, description, old_version, skill_type = row

        # AST validation
        if skill_type == "code" and new_code.strip():
            is_safe, violations = self._sandbox.validate_code(new_code)
            if not is_safe:
                msgs = [f"  Line {v.line}: {v.message}" for v in violations]
                return False, "New code validation failed:\n" + "\n".join(msgs)

        now = datetime.now(tz=timezone.utc).isoformat()
        new_version = old_version + 1

        # Save old version for rollback
        conn.execute(
            """INSERT INTO skill_versions
               (skill_id, version, code, description, changed_at, change_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (skill_id, old_version, old_code, description, now, change_reason),
        )

        # Update main record
        conn.execute(
            """UPDATE skill_forge
               SET code = ?, version = ?, status = 'approved',
                   updated_at = ?, update_description = ?
               WHERE id = ?""",
            (new_code, new_version, now, change_reason, skill_id),
        )
        conn.commit()

        # Write new file
        if skill_type == "code" and new_code.strip():
            self._write_skill_file(skill_name, new_code)

        self._sandbox.circuit_breaker.reset(skill_name)
        log.info("[skill-forge] updated skill '%s' to v%d", skill_name, new_version)
        return True, f"Skill '{skill_name}' updated to v{new_version}."

    def rollback_skill(self, skill_name: str) -> tuple[bool, str]:
        """Rollback a skill to its previous version.

        Reads the latest entry from skill_versions and restores it.
        """
        conn = self._ensure()
        row = conn.execute(
            "SELECT id, version, skill_type FROM skill_forge WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return False, f"Skill '{skill_name}' not found."

        skill_id, current_version, skill_type = row

        # Find previous version
        prev = conn.execute(
            """SELECT version, code, description FROM skill_versions
               WHERE skill_id = ? ORDER BY version DESC LIMIT 1""",
            (skill_id,),
        ).fetchone()
        if not prev:
            return False, f"No previous version of '{skill_name}' to rollback to."

        prev_version, prev_code, prev_desc = prev
        now = datetime.now(tz=timezone.utc).isoformat()

        conn.execute(
            """UPDATE skill_forge
               SET code = ?, version = ?, status = 'approved',
                   updated_at = ?, update_description = ?
               WHERE id = ?""",
            (prev_code, prev_version, now, f"Rollback from v{current_version}", skill_id),
        )
        # Remove the version entry we just restored from
        conn.execute(
            "DELETE FROM skill_versions WHERE skill_id = ? AND version = ?",
            (skill_id, prev_version),
        )
        conn.commit()

        if skill_type == "code" and prev_code:
            self._write_skill_file(skill_name, prev_code)

        self._sandbox.circuit_breaker.reset(skill_name)
        log.info("[skill-forge] rolled back '%s' to v%d", skill_name, prev_version)
        return True, f"Skill '{skill_name}' rolled back to v{prev_version}."

    # ── Queries ──────────────────────────────────────────────────────────

    def list_skills(self, *, status: str | None = None) -> list[dict]:
        """List all skills, optionally filtered by status."""
        conn = self._ensure()
        if status:
            rows = conn.execute(
                """SELECT id, status, skill_name, skill_type, description,
                          version, complexity, use_count, failure_count,
                          created_at, last_used
                   FROM skill_forge WHERE status = ?
                   ORDER BY skill_name""",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, status, skill_name, skill_type, description,
                          version, complexity, use_count, failure_count,
                          created_at, last_used
                   FROM skill_forge
                   ORDER BY skill_name""",
            ).fetchall()

        return [
            {
                "id": r[0], "status": r[1], "name": r[2], "type": r[3],
                "description": r[4], "version": r[5], "complexity": r[6],
                "use_count": r[7], "failure_count": r[8],
                "created_at": r[9], "last_used": r[10],
            }
            for r in rows
        ]

    def get_skill(self, skill_name: str) -> dict | None:
        """Get full details of a skill by name."""
        conn = self._ensure()
        row = conn.execute(
            """SELECT id, status, skill_name, skill_type, description, code,
                      trigger_patterns, auto_trigger, test_result,
                      version, parent_skill_id, complexity,
                      parts_total, parts_completed,
                      created_at, proposed_at, approved_at,
                      rejected_at, rejection_reason,
                      updated_at, update_description,
                      source_type, source_data,
                      use_count, last_used, failure_count,
                      generation_attempts
               FROM skill_forge WHERE skill_name = ?""",
            (skill_name,),
        ).fetchone()
        if not row:
            return None

        return {
            "id": row[0], "status": row[1], "name": row[2], "type": row[3],
            "description": row[4], "code": row[5],
            "trigger_patterns": json.loads(row[6]) if row[6] else [],
            "auto_trigger": row[7], "test_result": row[8],
            "version": row[9], "parent_skill_id": row[10],
            "complexity": row[11],
            "parts_total": row[12], "parts_completed": row[13],
            "created_at": row[14], "proposed_at": row[15],
            "approved_at": row[16],
            "rejected_at": row[17], "rejection_reason": row[18],
            "updated_at": row[19], "update_description": row[20],
            "source_type": row[21], "source_data": row[22],
            "use_count": row[23], "last_used": row[24],
            "failure_count": row[25],
            "generation_attempts": row[26],
        }

    def get_skill_history(self, skill_name: str) -> list[dict]:
        """Get version history for a skill."""
        conn = self._ensure()
        row = conn.execute(
            "SELECT id FROM skill_forge WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return []

        versions = conn.execute(
            """SELECT version, description, changed_at, change_reason
               FROM skill_versions WHERE skill_id = ?
               ORDER BY version DESC""",
            (row[0],),
        ).fetchall()

        return [
            {
                "version": v[0], "description": v[1],
                "changed_at": v[2], "change_reason": v[3],
            }
            for v in versions
        ]

    def record_skill_use(self, skill_name: str, *, success: bool = True) -> None:
        """Record that a skill was used (for stale/efficiency tracking)."""
        try:
            conn = self._ensure()
            now = datetime.now(tz=timezone.utc).isoformat()
            if success:
                conn.execute(
                    """UPDATE skill_forge
                       SET use_count = use_count + 1, last_used = ?
                       WHERE skill_name = ?""",
                    (now, skill_name),
                )
            else:
                conn.execute(
                    """UPDATE skill_forge
                       SET use_count = use_count + 1,
                           failure_count = failure_count + 1,
                           last_used = ?
                       WHERE skill_name = ?""",
                    (now, skill_name),
                )
            conn.commit()
        except Exception:
            log.debug("[skill-forge] record_skill_use failed", exc_info=True)

    # ── File system helpers ──────────────────────────────────────────────

    def _write_skill_file(self, name: str, code: str) -> None:
        """Write a skill's code to the user_generated directory."""
        try:
            self._skills_dir.mkdir(parents=True, exist_ok=True)
            path = self._skills_dir / f"{name}.py"
            path.write_text(code, encoding="utf-8")
            log.debug("[skill-forge] wrote %s", path)
        except Exception:
            log.warning("[skill-forge] failed to write skill file", exc_info=True)

    def _remove_skill_file(self, name: str) -> None:
        """Remove a skill's file from the user_generated directory."""
        try:
            path = self._skills_dir / f"{name}.py"
            if path.exists():
                path.unlink()
                log.debug("[skill-forge] removed %s", path)
        except Exception:
            log.warning("[skill-forge] failed to remove skill file", exc_info=True)
