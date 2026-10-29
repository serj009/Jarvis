"""Skill decomposer — estimates complexity and breaks large skills into parts.

Problem: Jarvis conceived a skill but it is too large for a single LLM
generation (~2000 token budget).  Without decomposition — loops or broken code.

Solution: ``SkillDecomposer`` estimates complexity *without* LLM calls (pure
heuristic), splits into manageable parts, and gates overly ambitious skills.

Lifecycle of a complex skill:
    1. SkillForge detects a need
    2. Decomposer estimates complexity (zero LLM, instant)
    3. SIMPLE → generate in one go (1 LLM call)
    4. MEDIUM → 2-3 parts, one per day
    5. COMPLEX → separate sub-skills, each through full lifecycle
    6. > 5 parts → abort with explanation
    7. Each part validated by AST independently
    8. Final assembly only when ALL parts pass

Loop protection:
    - Max 3 generation attempts per part
    - Max 3 days per whole skill
    - Max 5 LLM calls per skill
    - Failed dependency → abort entire skill
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class ComplexityLevel(Enum):
    """Skill complexity tiers."""
    SIMPLE = "simple"    # < 1500 tokens, 1 LLM call
    MEDIUM = "medium"    # 1500-3000 tokens, 2-3 parts
    COMPLEX = "complex"  # > 3000 tokens, separate sub-skills


@dataclass(frozen=True, slots=True)
class SkillNeed:
    """Describes a detected skill need from analysis."""
    name: str
    description: str
    skill_type: str                         # "macro" | "code"
    tools_involved: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    has_conditionals: bool = False
    has_state: bool = False
    trigger_patterns: list[str] = field(default_factory=list)
    source_type: str = ""                   # "pattern" | "error" | "user"
    source_data: str = ""


@dataclass(frozen=True, slots=True)
class SkillComplexity:
    """Complexity assessment for a skill need."""
    level: ComplexityLevel
    estimated_tokens: int
    estimated_parts: int
    reasoning: str


@dataclass(frozen=True, slots=True)
class SkillPart:
    """One piece of a decomposed skill."""
    index: int                              # 0-based
    total: int
    name: str
    description: str
    focus: str                              # what this part does
    depends_on: list[int] = field(default_factory=list)  # indices


class SkillDecomposer:
    """Estimates skill complexity and decomposes large skills into parts.

    All estimation is heuristic (zero LLM calls, instant).
    """

    MAX_SINGLE_SKILL_TOKENS: int = 1500
    MAX_STEPS_PER_MACRO: int = 10
    MAX_LINES_PER_CODE_SKILL: int = 100
    MAX_PARTS: int = 5
    MAX_GENERATION_ATTEMPTS_PER_PART: int = 3
    MAX_DAYS_PER_SKILL: int = 3
    MAX_LLM_CALLS_PER_SKILL: int = 5

    def estimate_complexity(self, skill_need: SkillNeed) -> SkillComplexity:
        """Estimate the complexity of a skill need WITHOUT any LLM calls.

        Heuristics used:
          - Number of tools involved
          - Number of steps in sequence
          - Conditional logic (if/else)
          - State between calls
          - Description length as a token proxy

        Returns a :class:`SkillComplexity` with level, estimated tokens
        and parts.
        """
        score = 0
        reasons: list[str] = []

        n_tools = len(skill_need.tools_involved)
        n_steps = len(skill_need.steps)

        # Tool count contribution
        if n_tools <= 1:
            score += 1
        elif n_tools <= 3:
            score += 2
            reasons.append(f"{n_tools} tools")
        else:
            score += 4
            reasons.append(f"{n_tools} tools (complex)")

        # Step count contribution
        if n_steps <= 2:
            score += 1
        elif n_steps <= 5:
            score += 2
            reasons.append(f"{n_steps} steps")
        else:
            score += 4
            reasons.append(f"{n_steps} steps (many)")

        # Conditional logic
        if skill_need.has_conditionals:
            score += 2
            reasons.append("conditional logic")

        # State between calls
        if skill_need.has_state:
            score += 2
            reasons.append("stateful")

        # Token estimation from description
        desc_words = len(skill_need.description.split())
        estimated_tokens = max(200, desc_words * 15 + n_tools * 100 + n_steps * 80)
        if skill_need.has_conditionals:
            estimated_tokens = int(estimated_tokens * 1.3)
        if skill_need.has_state:
            estimated_tokens = int(estimated_tokens * 1.2)

        # Determine level
        if score <= 4 and estimated_tokens <= self.MAX_SINGLE_SKILL_TOKENS:
            level = ComplexityLevel.SIMPLE
            parts = 1
        elif score <= 7 and estimated_tokens <= 3000:
            level = ComplexityLevel.MEDIUM
            parts = min(3, max(2, estimated_tokens // 1200))
        else:
            level = ComplexityLevel.COMPLEX
            parts = max(3, estimated_tokens // 1000)

        reasoning = "; ".join(reasons) if reasons else "straightforward"
        return SkillComplexity(
            level=level,
            estimated_tokens=estimated_tokens,
            estimated_parts=parts,
            reasoning=reasoning,
        )

    def decompose(self, skill_need: SkillNeed) -> list[SkillPart]:
        """Break a skill need into parts based on complexity.

        SIMPLE: 1 part (generate immediately).
        MEDIUM: 2-3 parts (core logic → edge cases → integration).
        COMPLEX: Separate independent sub-skills + macro orchestrator.

        Returns a list of :class:`SkillPart`.
        """
        complexity = self.estimate_complexity(skill_need)

        if complexity.level == ComplexityLevel.SIMPLE:
            return [SkillPart(
                index=0, total=1,
                name=skill_need.name,
                description=skill_need.description,
                focus="complete implementation",
            )]

        if complexity.level == ComplexityLevel.MEDIUM:
            return self._decompose_medium(skill_need, complexity)

        return self._decompose_complex(skill_need, complexity)

    def should_abort(self, skill_need: SkillNeed) -> tuple[bool, str]:
        """Check if a skill is too ambitious for auto-generation.

        Returns ``(should_abort, reason)`` — True + reason if the skill
        should NOT be attempted.

        Abort conditions:
          - > 5 parts → too complex
          - Requires blacklisted modules
          - Would require > 3 LLM calls for MEDIUM, > 5 for COMPLEX
        """
        complexity = self.estimate_complexity(skill_need)

        if complexity.estimated_parts > self.MAX_PARTS:
            return True, (
                f"Skill requires ~{complexity.estimated_parts} parts "
                f"(max {self.MAX_PARTS}). Too complex for auto-generation. "
                "I can describe what's needed, and you can create it manually."
            )

        # Check for blocked module hints in description / steps
        from jarvis.skills.sandbox import BLOCKED_IMPORTS
        desc_lower = skill_need.description.lower()
        for mod in BLOCKED_IMPORTS:
            if mod in desc_lower and mod not in ("io", "re"):
                return True, (
                    f"Skill would require '{mod}' module which is blocked. "
                    "Skills cannot use system/network modules directly."
                )

        # LLM call budget check
        if complexity.level == ComplexityLevel.MEDIUM:
            # Each part = 1 LLM call + potential retry = 2 max
            max_calls = complexity.estimated_parts * 2
            if max_calls > self.MAX_LLM_CALLS_PER_SKILL:
                return True, (
                    f"Skill would require up to {max_calls} LLM calls "
                    f"(budget: {self.MAX_LLM_CALLS_PER_SKILL}). "
                    "Consider simplifying the requirements."
                )

        return False, ""

    def _decompose_medium(
        self, need: SkillNeed, complexity: SkillComplexity,
    ) -> list[SkillPart]:
        """Decompose a MEDIUM-complexity skill into 2-3 parts."""
        parts: list[SkillPart] = []
        total = complexity.estimated_parts

        # Part 1: Core logic
        parts.append(SkillPart(
            index=0, total=total,
            name=f"{need.name}_core",
            description=f"Core logic for {need.description}",
            focus="main functionality, happy path",
        ))

        # Part 2: Edge cases + error handling
        parts.append(SkillPart(
            index=1, total=total,
            name=f"{need.name}_errors",
            description=f"Error handling for {need.description}",
            focus="edge cases, validation, error messages",
            depends_on=[0],
        ))

        # Part 3 (if needed): Voice patterns + integration
        if total >= 3:
            parts.append(SkillPart(
                index=2, total=total,
                name=f"{need.name}_integration",
                description=f"Triggers and integration for {need.description}",
                focus="voice patterns, trigger registration, testing",
                depends_on=[0, 1],
            ))

        return parts

    def _decompose_complex(
        self, need: SkillNeed, complexity: SkillComplexity,
    ) -> list[SkillPart]:
        """Decompose a COMPLEX skill into separate sub-skills + orchestrator."""
        parts: list[SkillPart] = []
        # Cap at MAX_PARTS for actual decomposition (uncapped in estimate for should_abort).
        total = min(self.MAX_PARTS, complexity.estimated_parts)

        # Split tools/steps among sub-skills
        tools = need.tools_involved
        steps = need.steps
        chunk_size = max(1, len(steps) // (total - 1)) if total > 1 else len(steps)

        for i in range(total - 1):
            step_slice = steps[i * chunk_size:(i + 1) * chunk_size]
            step_desc = "; ".join(step_slice) if step_slice else f"sub-task {i + 1}"
            parts.append(SkillPart(
                index=i, total=total,
                name=f"{need.name}_part{i + 1}",
                description=f"Sub-skill: {step_desc}",
                focus=step_desc,
                depends_on=[j for j in range(i)],  # chain dependency
            ))

        # Final part: orchestrator macro
        parts.append(SkillPart(
            index=total - 1, total=total,
            name=need.name,
            description="Orchestrator: calls sub-skills in sequence",
            focus="macro: call sub-skills, pass results between them",
            depends_on=list(range(total - 1)),
        ))

        return parts
