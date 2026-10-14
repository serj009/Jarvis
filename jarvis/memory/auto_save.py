"""Automatic fact extraction from conversations.

After each completed response chain (user said X, Jarvis answered Y),
extract key facts worth remembering:
- User preferences ("I like jazz", "my favorite game is Stalker")
- Named entities (people, places, projects mentioned by user)
- Decisions ("let's use Chrome for that", "remind me about X")
- Corrections ("no, my name is Serhii not Sergey")

Strategy: Simple heuristic extraction (not LLM-based — too slow).
Pattern-match common fact-bearing phrases in EN / RU / UK.

Fire-and-forget: called as ``asyncio.create_task`` after the response
chain completes so it never blocks the pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns that indicate a fact worth remembering
# ---------------------------------------------------------------------------

_FACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # -- Preferences --------------------------------------------------------
    # EN: "I like X", "my favorite X is Y", "I prefer X"
    (re.compile(
        r"(?:i\s+like|my\s+favou?rite|i\s+prefer|i\s+love)\s+(.+)",
        re.IGNORECASE,
    ), "preference"),
    # RU: "я люблю X", "мне нравится X", "мой любимый X"
    (re.compile(
        r"(?:я\s+люблю|мне\s+нравится|мой\s+любимый|моя\s+любимая|моё?\s+любимое)\s+(.+)",
        re.IGNORECASE,
    ), "preference"),
    # UK: "мені подобається X", "мій улюблений X"
    (re.compile(
        r"(?:мені\s+подобається|мій\s+улюблений|моя\s+улюблена|моє\s+улюблене)\s+(.+)",
        re.IGNORECASE,
    ), "preference"),

    # -- Identity / names ---------------------------------------------------
    # EN: "my name is X", "I'm X", "call me X"
    (re.compile(
        r"(?:my\s+name\s+is|i'?m|call\s+me)\s+(\w+)",
        re.IGNORECASE,
    ), "identity"),
    # RU: "меня зовут X", "я — X"
    (re.compile(
        r"(?:меня\s+зовут|я\s+—?\s*)(\w+)",
        re.IGNORECASE,
    ), "identity"),
    # UK: "мене звати X"
    (re.compile(
        r"(?:мене\s+звати|я\s+—?\s*)(\w+)",
        re.IGNORECASE,
    ), "identity"),

    # -- Explicit memory requests (slip-through from RememberTool) ----------
    (re.compile(
        r"(?:запомни|не\s+забудь|remember|don'?t\s+forget)\s+(?:что\s+|що\s+|that\s+)?(.+)",
        re.IGNORECASE,
    ), "explicit"),

    # -- Location -----------------------------------------------------------
    # EN: "I live in X", "I'm from X"
    (re.compile(
        r"(?:i\s+live\s+in|i'?m\s+from)\s+(.+)",
        re.IGNORECASE,
    ), "location"),
    # RU: "я живу в X", "я из X"
    (re.compile(
        r"(?:я\s+живу\s+в|я\s+из)\s+(.+)",
        re.IGNORECASE,
    ), "location"),
    # UK: "я живу в X", "я з X"
    (re.compile(
        r"(?:я\s+живу\s+в|я\s+з)\s+(.+)",
        re.IGNORECASE,
    ), "location"),

    # -- Work / profession --------------------------------------------------
    (re.compile(
        r"(?:i\s+work\s+(?:at|for|as)|я\s+работаю|я\s+працюю)\s+(.+)",
        re.IGNORECASE,
    ), "work"),
]

# Minimum length of the extracted match to be saved (avoids noise).
_MIN_FACT_LENGTH = 4


def extract_facts(user_utterance: str) -> list[tuple[str, str]]:
    """Extract memorable facts from a user utterance.

    Returns a list of ``(fact_text, category)`` tuples.
    """
    facts: list[tuple[str, str]] = []
    for pattern, category in _FACT_PATTERNS:
        m = pattern.search(user_utterance)
        if m:
            fact = m.group(1).strip().rstrip(".,!?")
            if len(fact) >= _MIN_FACT_LENGTH:
                facts.append((fact, category))
    return facts


async def auto_save_facts(
    memory_store: MemoryStore,
    user_utterance: str,
    assistant_response: str | None = None,
) -> int:
    """Extract and save facts from a conversation turn.

    Returns the number of new facts saved.

    This function is designed to be called via
    ``asyncio.create_task(auto_save_facts(...))`` — fire-and-forget,
    never blocks the pipeline.
    """
    if not memory_store.is_open:
        return 0

    facts = extract_facts(user_utterance)
    if not facts:
        return 0

    saved = 0
    for fact_text, category in facts:
        try:
            # Dedup: check if this fact already exists
            existing = await memory_store.search(fact_text, limit=1)
            if existing:
                # Simple substring similarity check
                existing_lower = existing[0].text.lower()
                fact_lower = fact_text.lower()
                if fact_lower in existing_lower or existing_lower in fact_lower:
                    continue  # already know this

            # Save with auto_ tag prefix for traceability
            full_fact = f"User said: {fact_text}"
            await memory_store.add(
                full_fact,
                category=category,
                source="conversation",
                tags=[f"auto_{category}"],
            )
            saved += 1
            log.debug("[auto-save] saved fact: %s (category=%s)", fact_text[:60], category)
        except Exception:
            log.warning("[auto-save] failed to save fact: %s", fact_text[:60], exc_info=True)

    if saved:
        log.info("[auto-save] extracted and saved %d facts from utterance", saved)
    return saved
