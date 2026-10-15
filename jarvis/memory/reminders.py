"""Event-triggered reminders.

Reminders are stored in memory with special tags and trigger conditions.
When the trigger event happens (game starts, app opens), the reminder
is surfaced to the user.

Trigger types:
  - app_launch: fires when specific app is opened (open_app tool)
  - game_launch: fires when specific game is launched (launch_steam_game)
  - game_mode: fires when game mode activates for a specific game
  - next_session: fires on next wake-word activation

Storage in memory:
  text: "REMINDER: Найти Flame of the Fell God"
  tags: ["reminder", "trigger:elden_ring"]
  category: "reminder"

Detection flow:
  1. auto_save.py detects "напомни когда ..." → parse_reminder()
  2. save_reminder() stores fact with trigger tag
  3. intent_router._run_tools() → after launch_steam_game / open_app →
     check_reminders(app_name) → yield SpeakIntent with reminders
  4. game_mode._preload_game_context() → check_reminders(game_title) →
     inject into preloaded context
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag prefix for trigger-based reminders in memory.
# Full tag format: "trigger:<normalized_target>"
# Example: "trigger:elden_ring", "trigger:chrome", "trigger:steam"
# ---------------------------------------------------------------------------
_TRIGGER_TAG_PREFIX = "trigger:"

# Text prefix that marks a fact as a reminder (vs. a regular fact).
_REMINDER_TEXT_PREFIX = "REMINDER: "

# ---------------------------------------------------------------------------
# Patterns to detect reminder requests (multi-language)
# ---------------------------------------------------------------------------

_REMINDER_PATTERNS: list[re.Pattern[str]] = [
    # RU: "напомни когда буду играть в X что Y"
    #     "напомни мне когда начну играть в X — Y"
    re.compile(
        r"(?:напомни|напомни\s+мне)\s+"
        r"(?:когда|если)\s+(?:буду|начну|запущу)\s+"
        r"(?:играть\s+в|игру)\s+(.+?)\s+"
        r"(?:что|—)\s+(.+)",
        re.I,
    ),
    # RU: "напомни когда открою X что Y"
    #     "напомни если запущу X — Y"
    re.compile(
        r"(?:напомни|напомни\s+мне)\s+"
        r"(?:когда|если)\s+(?:открою|запущу)\s+(.+?)\s+"
        r"(?:—|что)\s+(.+)",
        re.I,
    ),
    # RU: "напомни при следующем запуске X что Y"
    re.compile(
        r"(?:напомни|напомни\s+мне)\s+"
        r"(?:при\s+(?:следующем|очередном)\s+(?:запуске|открытии))\s+(.+?)\s+"
        r"(?:что|—)\s+(.+)",
        re.I,
    ),
    # EN: "remind me when I play X to Y"
    #     "remind me next time I launch X that Y"
    re.compile(
        r"(?:remind\s+me)\s+"
        r"(?:when|if|next\s+time)\s+(?:I\s+)?(?:play|launch|open|start)\s+(.+?)\s+"
        r"(?:to|that)\s+(.+)",
        re.I,
    ),
    # UK: "нагадай коли буду грати в X що Y"
    re.compile(
        r"(?:нагадай|нагадай\s+мені)\s+"
        r"(?:коли|якщо)\s+(?:буду|почну|запущу)\s+"
        r"(?:грати\s+в|гру)\s+(.+?)\s+"
        r"(?:що|—)\s+(.+)",
        re.I,
    ),
    # UK: "нагадай коли відкрию X що Y"
    re.compile(
        r"(?:нагадай|нагадай\s+мені)\s+"
        r"(?:коли|якщо)\s+(?:відкрию|запущу)\s+(.+?)\s+"
        r"(?:що|—)\s+(.+)",
        re.I,
    ),
]


def _normalize_target(target: str) -> str:
    """Normalize a trigger target for tag storage.

    Lowercases, strips, replaces spaces/dashes with underscores.
    Example: "Elden Ring" → "elden_ring"
    """
    return target.lower().strip().replace(" ", "_").replace("-", "_")


def parse_reminder(utterance: str) -> tuple[str, str] | None:
    """Parse a reminder request from a user utterance.

    Returns ``(trigger_target, reminder_text)`` or ``None`` if the
    utterance is not a reminder request.

    Examples:
        >>> parse_reminder("напомни когда буду играть в Elden Ring что нужно найти Flame")
        ('Elden Ring', 'нужно найти Flame')
        >>> parse_reminder("remind me when I play Skyrim to find the Daedric sword")
        ('Skyrim', 'find the Daedric sword')
        >>> parse_reminder("open chrome")  # not a reminder
    """
    for pattern in _REMINDER_PATTERNS:
        m = pattern.search(utterance)
        if m:
            target = m.group(1).strip().rstrip(".,!?")
            text = m.group(2).strip().rstrip(".,!?")
            if target and text:
                return (target, text)
    return None


async def save_reminder(
    memory_store: MemoryStore,
    trigger_target: str,
    text: str,
) -> bool:
    """Save an event-triggered reminder to long-term memory.

    The reminder is stored as a fact with:
    - text: "REMINDER: <text>"
    - category: "reminder"
    - tags: ["reminder", "trigger:<normalized_target>"]

    Returns True on success, False on failure.
    """
    tag = _TRIGGER_TAG_PREFIX + _normalize_target(trigger_target)
    try:
        await memory_store.add(
            text=f"{_REMINDER_TEXT_PREFIX}{text}",
            category="reminder",
            source="user",
            tags=["reminder", tag],
        )
        log.info(
            "[reminders] saved: trigger=%s text=%s",
            trigger_target,
            text[:80],
        )
        return True
    except Exception:
        log.warning("[reminders] failed to save reminder", exc_info=True)
        return False


async def check_reminders(
    memory_store: MemoryStore,
    event_target: str,
) -> list[str]:
    """Check if there are pending reminders for a given event target.

    Called when an app/game is launched. Searches memory by the trigger
    tag and returns the list of reminder texts (without the REMINDER:
    prefix).

    Args:
        memory_store: The memory store to search.
        event_target: The app/game name that was just launched.
            Examples: "Elden Ring", "Chrome", "Steam"

    Returns:
        List of reminder text strings. Empty if no reminders found.
    """
    normalized = _normalize_target(event_target)
    tag = _TRIGGER_TAG_PREFIX + normalized

    try:
        # Search by tag for exact trigger match
        results = await memory_store.search(
            "REMINDER", category="reminder", tag=tag,
            limit=20,
        )

        reminders: list[str] = []
        for fact in results:
            text = fact.text
            # Also check that the trigger tag is actually present
            # (FTS may return fuzzy matches)
            if tag not in fact.tags:
                continue
            if text.startswith(_REMINDER_TEXT_PREFIX):
                reminders.append(text[len(_REMINDER_TEXT_PREFIX):])
            elif "REMINDER:" in text:
                # Fallback: extract after REMINDER: marker
                idx = text.index("REMINDER:")
                reminders.append(text[idx + len("REMINDER:"):].strip())

        if reminders:
            log.info(
                "[reminders] found %d reminder(s) for '%s'",
                len(reminders),
                event_target,
            )
        return reminders
    except Exception:
        log.warning(
            "[reminders] failed to check reminders for '%s'",
            event_target,
            exc_info=True,
        )
        return []


async def clear_reminder(
    memory_store: MemoryStore,
    event_target: str,
    reminder_text: str,
) -> bool:
    """Delete a fired reminder from memory (one-shot reminders).

    After a reminder is spoken, remove it so it doesn't fire again
    on every subsequent launch. Returns True if deleted.
    """
    normalized = _normalize_target(event_target)
    tag = _TRIGGER_TAG_PREFIX + normalized

    try:
        results = await memory_store.search(
            reminder_text, category="reminder", limit=5,
        )
        for fact in results:
            if tag in fact.tags and reminder_text in fact.text:
                await memory_store.delete(fact.id)
                log.info("[reminders] cleared fired reminder: %s", fact.id)
                return True
        return False
    except Exception:
        log.warning("[reminders] failed to clear reminder", exc_info=True)
        return False


def format_reminders_for_speech(reminders: list[str], lang: str = "en") -> str:
    """Format reminder list into a spoken string.

    Args:
        reminders: List of reminder texts.
        lang: Language code ("en", "ru", "uk").

    Returns:
        Formatted spoken string.
    """
    if not reminders:
        return ""

    if len(reminders) == 1:
        prefixes = {
            "en": "Sir, a reminder:",
            "ru": "Сэр, напоминание:",
            "uk": "Сер, нагадування:",
        }
        prefix = prefixes.get(lang, prefixes["en"])
        return f"{prefix} {reminders[0]}"

    prefixes = {
        "en": f"Sir, you have {len(reminders)} reminders.",
        "ru": f"Сэр, у вас {len(reminders)} напоминаний.",
        "uk": f"Сер, у вас {len(reminders)} нагадувань.",
    }
    prefix = prefixes.get(lang, prefixes["en"])
    items = " ".join(
        f"{i}. {r}." for i, r in enumerate(reminders, 1)
    )
    return f"{prefix} {items}"
