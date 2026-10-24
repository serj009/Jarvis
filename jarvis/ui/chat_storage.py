"""Persistent JSON storage for chat messages.

Each message is a ``ChatMessage`` dataclass with role, text, and timestamp.
``ChatStorage`` serialises a list to a single JSON file so history survives
across sessions.

Thread-safety
-------------
All public methods acquire a threading lock, so the audio thread (which
produces JARVIS responses) and the Qt main thread (which reads for display)
can share one ``ChatStorage`` instance without corruption.

File format
-----------
The file is a JSON array of message dicts, oldest-first (chronological).
Datetimes are stored as ISO-8601 strings (UTC).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_FILE = "chat_history.json"
_MAX_MESSAGES = 500  # hard cap to prevent unbounded file growth


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """One chat message."""

    role: str = "user"  # "user" | "assistant" | "system"
    text: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _msg_to_dict(msg: ChatMessage) -> dict:
    return asdict(msg)


def _msg_from_dict(d: dict) -> ChatMessage:
    return ChatMessage(
        role=d.get("role", "user"),
        text=d.get("text", ""),
        timestamp=d.get("timestamp", ""),
    )


# ---------------------------------------------------------------------------
# ChatStorage
# ---------------------------------------------------------------------------


class ChatStorage:
    """JSON-backed persistent chat history.

    Parameters
    ----------
    path : Path
        Full path to the JSON file.  Created on first write.
    max_messages : int
        Maximum messages retained.  Oldest are trimmed on save.
    """

    def __init__(
        self,
        path: Path | None = None,
        max_messages: int = _MAX_MESSAGES,
    ) -> None:
        if path is None:
            from jarvis.paths import data_path

            # Layout unchanged: <data_dir>/data/chat_history.json.
            path = data_path("data", _DEFAULT_FILE)
        self._path = path
        self._max = max_messages
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # -- public API --------------------------------------------------------

    def save_message(self, msg: ChatMessage) -> None:
        """Append a single message and persist."""
        with self._lock:
            messages = self._read_locked()
            messages.append(msg)
            # Trim oldest if over capacity
            if len(messages) > self._max:
                messages = messages[-self._max:]
            self._write_locked(messages)

    def load_history(self, limit: int = 100) -> list[ChatMessage]:
        """Load the most recent *limit* messages (chronological order)."""
        with self._lock:
            messages = self._read_locked()
        if limit and len(messages) > limit:
            return messages[-limit:]
        return messages

    def clear(self) -> None:
        """Delete all messages."""
        with self._lock:
            self._write_locked([])

    def message_count(self) -> int:
        """Total number of persisted messages."""
        with self._lock:
            return len(self._read_locked())

    # -- internal ----------------------------------------------------------

    def _read_locked(self) -> list[ChatMessage]:
        """Read from disk.  Caller holds self._lock."""
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                log.warning("chat history is not a list; returning empty")
                return []
            return [_msg_from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("failed to load chat history: %s", exc)
            return []

    def _write_locked(self, messages: list[ChatMessage]) -> None:
        """Write to disk atomically.  Caller holds self._lock."""
        try:
            from jarvis.core.fileio import atomic_write_json

            atomic_write_json(
                self._path,
                [_msg_to_dict(m) for m in messages],
                ensure_ascii=False,
                indent=2,
            )
        except OSError:
            log.exception("failed to persist chat history")
