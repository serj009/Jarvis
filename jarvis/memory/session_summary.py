"""Session Summary — saves conversation summary to long-term memory on session end.

When a conversation session ends (wake-word timeout → maybe_clear() in
conversation.py), the session's turns are condensed into a summary and
saved to long-term memory.  This gives Jarvis cross-session awareness:

  "Session 2026-10-15 14:30: User asked: открой хром; какая погода.
   Tools used: open_app, get_weather"

The summary is saved as a memory fact with tags=["session_summary"] and
category="session", making it searchable via auto_recall.

This means when the user says "что мы вчера делали?" → auto_recall finds
the session summary → Jarvis can answer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)

MAX_SUMMARY_LENGTH = 500

# Minimum user turns to consider a session worth summarising.
_MIN_USER_TURNS = 2


def summarize_session(turns: list[dict]) -> str | None:
    """Summarize a conversation session into a brief text.

    Extracts: user requests, tool calls, key topics.
    Returns None if session too short (< 2 user turns).
    """
    user_turns = [t for t in turns if t.get("role") == "user"]
    if len(user_turns) < _MIN_USER_TURNS:
        return None  # too short to summarize

    parts: list[str] = []

    # Collect user requests
    user_topics: list[str] = []
    for t in turns:
        if t.get("role") == "user":
            content = t.get("content", "")
            if content:
                short = content[:80] + "..." if len(content) > 80 else content
                user_topics.append(short)

    # Collect tool calls from assistant messages with tool_calls
    tools_used: list[str] = []
    for t in turns:
        if t.get("role") == "assistant":
            tool_calls = t.get("tool_calls", [])
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                name = func.get("name", "")
                if name:
                    tools_used.append(name)

    # Build summary
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M")

    parts.append(f"Session {date_str}:")

    if user_topics:
        topics_str = "; ".join(user_topics[:5])
        parts.append(f"User asked: {topics_str}")

    if tools_used:
        unique_tools = list(dict.fromkeys(tools_used))[:5]  # preserve order, dedup
        parts.append(f"Tools used: {', '.join(unique_tools)}")

    summary = " ".join(parts)
    if len(summary) > MAX_SUMMARY_LENGTH:
        summary = summary[:MAX_SUMMARY_LENGTH - 3] + "..."

    return summary


async def save_session_summary(
    memory_store: MemoryStore,
    turns: list[dict],
) -> bool:
    """Summarize and save session to long-term memory.

    Returns True if saved, False if session too short or error.
    """
    summary = summarize_session(turns)
    if summary is None:
        return False

    try:
        await memory_store.add(
            text=summary,
            tags=["session_summary"],
            category="session",
            source="system",
        )
        log.info("session summary saved: %s", summary[:100])
        return True
    except Exception:
        log.warning("session_summary: failed to save", exc_info=True)
        return False
