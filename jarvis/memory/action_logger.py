"""Action Logger — records what the user DID (not just what they said).

After each successful tool call, saves an action record to long-term memory.
This lets Jarvis know contextual history:
  - "Пользователь запустил Mafia 2 через Steam" → next time "запусти ту игру" → Mafia 2
  - "Пользователь открыл Chrome 5 раз сегодня" → knows Chrome is frequently used
  - "Пользователь проверял погоду в 08:15" → learns morning routines

Unlike tool_examples.py (which stores utterance→tool patterns for LLM few-shot),
action_logger stores FACTS about user behavior for memory recall.

Records format in memory:
  text: "Opened Chrome via open_app"
  tags: ["action", "open_app"]
  category: "action"
  source: "system"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)

# Human-readable action templates per tool
_ACTION_TEMPLATES: dict[str, str] = {
    "open_app": "Opened {app_name}",
    "close_app": "Closed {app_name}",
    "play_youtube_music": "Played music: {query}",
    "launch_steam_game": "Launched game: {game_name} on Steam",
    "open_url": "Searched for: {query}",
    "get_weather": "Checked weather",
    "screenshot": "Took a screenshot",
    "see_screen": "Looked at screen",
    "lock_screen": "Locked the screen",
    "launch_workspace": "Opened workspace",
    "take_note": "Created a note: {title}",
    "remember": "Asked to remember something",
    "deep_research": "Started deep research: {query}",
    "research": "Researched: {query}",
    "volume": "Changed volume: {action}",
    "get_time": "Asked what time it is",
}

# Tools NOT worth logging (too noisy / internal)
_SKIP_TOOLS: frozenset[str] = frozenset({
    "clipboard", "list_directory", "memory_status", "recall",
    "show_dashboard", "close_dashboard", "show_logs", "close_logs",
    "open_help", "open_notes", "close_notes", "close_research",
    "read_more", "copy_research", "show_clipboard_history",
    "close_clipboard_history", "clear_clipboard_history",
    "paste_clipboard_item", "switch_language", "forget",
    "read_note", "append_to_note", "delete_note",
    "pause_deep_research", "resume_deep_research",
    "close_deep_research", "delete_deep_research",
    "delete_all_deep_research", "enable_deep_research_ultra",
    "disable_deep_research_ultra",
})


def format_action(tool_name: str, args: dict) -> str | None:
    """Format a tool call as a human-readable action string.

    Returns None if tool should be skipped (too noisy).
    """
    if tool_name in _SKIP_TOOLS:
        return None

    template = _ACTION_TEMPLATES.get(tool_name)
    if template is None:
        # Generic fallback for unknown tools
        return f"Used {tool_name}"

    try:
        return template.format(**args)
    except (KeyError, TypeError):
        return template.split(":")[0] if ":" in template else template


async def log_action(
    memory_store: MemoryStore,
    tool_name: str,
    args: dict,
    success: bool = True,
) -> None:
    """Save a user action to long-term memory.

    Only logs successful actions. Fire-and-forget — never blocks pipeline.
    """
    if not success:
        return

    action_text = format_action(tool_name, args)
    if action_text is None:
        return

    try:
        await memory_store.add(
            text=action_text,
            tags=["action", tool_name],
            category="action",
            source="system",
        )
        log.debug("action logged: %s", action_text)
    except Exception:
        log.warning("action_logger: failed to save", exc_info=True)
