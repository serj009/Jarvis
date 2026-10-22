"""SkillForge — Jarvis self-generates skills by observing usage patterns.

Public surface:
  - SkillForge:         the observation → analysis → generation engine
  - SkillSandbox:       5-level protection for user-generated code
  - SkillValidator:     AST validation (Level 3)
  - SkillDecomposer:    breaks large skills into safe pieces
  - SkillComplexity:    complexity estimation result
  - SkillNeed:          detected skill need descriptor
  - SkillPart:          one piece of a decomposed skill

User-generated skill files live under ``jarvis/skills/user_generated/``.
Deleting that directory returns Jarvis to its built-in-only baseline.
"""

from jarvis.skills.sandbox import SkillSandbox, SkillValidator
from jarvis.skills.decomposer import SkillDecomposer, SkillComplexity, SkillNeed, SkillPart
from jarvis.skills.skill_forge import SkillForge

__all__ = [
    "SkillForge",
    "SkillSandbox",
    "SkillValidator",
    "SkillDecomposer",
    "SkillComplexity",
    "SkillNeed",
    "SkillPart",
]
