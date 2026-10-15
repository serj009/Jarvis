"""PersonalizationContext — single object carrying all personalization stores.

Replaces 7+ individual parameters in IntentRouter and other consumers.
Before: IntentRouter(memory_store=, relation_store=, tool_examples=,
                     personal_aliases=, user_profile=, usage_stats=, game_mode=)
After:  IntentRouter(personalization=PersonalizationContext(...))
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore
    from jarvis.memory.relations import RelationStore
    from jarvis.memory.tool_examples import ToolExampleStore
    from jarvis.memory.personal_aliases import PersonalAliasStore
    from jarvis.memory.user_profile import UserProfile
    from jarvis.memory.usage_stats import UsageStats
    from jarvis.core.game_mode import GameMode


@dataclass
class PersonalizationContext:
    """All personalization stores in one object.

    Every field is optional (None) — consumers check before use.
    This lets tests construct a minimal context without mocking everything.
    """

    memory_store: Any = None        # MemoryStore
    relation_store: Any = None      # RelationStore
    tool_examples: Any = None       # ToolExampleStore
    personal_aliases: Any = None    # PersonalAliasStore
    user_profile: Any = None        # UserProfile
    usage_stats: Any = None         # UsageStats
    game_mode: Any = None           # GameMode

    # Convenience: empty context for tests
    @classmethod
    def empty(cls) -> PersonalizationContext:
        """Return a context with every field set to None."""
        return cls()
