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
    from jarvis.core.game_mode import GameMode
    from jarvis.memory.personal_aliases import PersonalAliasStore
    from jarvis.memory.relations import RelationStore
    from jarvis.memory.store import MemoryStore
    from jarvis.memory.tool_examples import ToolExampleStore
    from jarvis.memory.usage_stats import UsageStats
    from jarvis.memory.user_profile import UserProfile


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
    audit_log: Any = None           # SecurityAuditLog (Layer 4; optional)

    # Convenience: empty context for tests
    @classmethod
    def empty(cls) -> PersonalizationContext:
        """Return a context with every field set to None."""
        return cls()


# Generic words that likely map to a specific app (kept in sync with
# IntentRouter._learn_personal_aliases). Used by record_tool_success below.
_GENERIC_ALIAS_WORDS = {
    "браузер", "browser", "редактор", "editor", "плеер", "player",
    "плеєр", "мессенджер", "messenger", "месенджер", "почта", "mail",
    "пошта", "музыка", "music", "музика", "архиватор", "archiver",
    "терминал", "terminal", "термінал", "ide", "іде",
}


def record_tool_success(
    pctx: PersonalizationContext,
    transcription: str,
    tool_name: str,
    args: dict,
) -> None:
    """Record one successful tool call into every personalization store.

    This is the single learning path for tool calls that do NOT run inside
    the router's feedback loop: pattern/snap commands ("open spotify",
    "volume up") and the one-shot dispatch mode (max_tool_iterations == 1).
    Both execute in app._make_router_adapter via execute_intent, where the
    ToolResult.success is known, and neither reaches IntentRouter's own
    recording path. Calling this from there closes that gap without the
    router having to execute the tool itself.

    Mirrors IntentRouter._record_tool_examples + _record_usage_stats +
    _learn_personal_aliases. Every step is independently guarded and never
    raises — learning must never break the voice pipeline.
    """
    import logging

    log = logging.getLogger(__name__)

    # 1. Few-shot example: utterance -> tool(args)
    if pctx.tool_examples is not None:
        try:
            pctx.tool_examples.record_success(transcription, tool_name, args)
        except Exception:
            log.warning("[personalization] tool-example record failed", exc_info=True)

    # 2. Usage stats: tool frequency + app frequency
    if pctx.usage_stats is not None:
        try:
            pctx.usage_stats.record_tool_use(tool_name)
            if tool_name == "open_app":
                app_name = args.get("name", "")
                if app_name:
                    pctx.usage_stats.record_app_use(app_name)
        except Exception:
            log.warning("[personalization] usage-stats record failed", exc_info=True)

    # 3. Personal aliases: generic word in utterance -> specific app arg
    if pctx.personal_aliases is not None and tool_name == "open_app":
        try:
            app_arg = args.get("name", "").strip().lower()
            if app_arg:
                transcription_lower = transcription.lower()
                for generic in _GENERIC_ALIAS_WORDS:
                    if generic in transcription_lower and generic != app_arg:
                        pctx.personal_aliases.learn(generic, app_arg)
                        log.info(
                            "[personalization] learned personal alias: %r -> %r",
                            generic, app_arg,
                        )
                        break
        except Exception:
            log.warning("[personalization] personal-alias learn failed", exc_info=True)
