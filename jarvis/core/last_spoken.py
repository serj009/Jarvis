"""Thread-safe store for the last text JARVIS spoke aloud.

Used by RepeatLastTool ("repeat that" / "повтори" / "що ти сказав?")
to replay the most recent response.

The store is intentionally decoupled from ConversationMemory:
conversation history is wiped on every wake-word detection
(``on_wake`` hook in ``app.py``), but the user might say "hey jarvis,
repeat that" *after* a wake — they want the previous answer, not an
empty buffer.  ``last_spoken`` therefore survives wake clears.

Write side:  ``pipeline.py`` calls ``set_last_spoken()`` right after
    assembling ``LLMResponseComplete`` — the single choke-point where
    all substantive spoken text flows.  Thinking phrases and error
    one-liners are NOT captured (they are not useful to repeat).

Read side:  ``RepeatLastTool.execute()`` calls ``get_last_spoken()``.

Thread safety: the GIL serialises simple attribute stores; an
``threading.Lock`` is added for belt-and-suspenders safety if the GIL
model ever changes.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_last_spoken: str | None = None


def set_last_spoken(text: str) -> None:
    """Record the text JARVIS just spoke."""
    global _last_spoken
    with _lock:
        _last_spoken = text


def get_last_spoken() -> str | None:
    """Return the last spoken text, or ``None`` if nothing has been spoken."""
    with _lock:
        return _last_spoken


def clear_last_spoken() -> None:
    """Reset the store.  Useful for tests."""
    global _last_spoken
    with _lock:
        _last_spoken = None
