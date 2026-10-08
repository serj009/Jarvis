"""Per-request context for the current user utterance.

The audio pipeline sets `current_user_transcription` around each
router/execute pass so tools can recover the full sentence when the LLM
passes a thin or empty tool argument.

It also sets `correlation_id` once per voice turn (roadmap T1.2), so every
log line produced while handling that turn -- STT, LLM, tool calls, TTS --
carries the same short id. ContextVars are copied into asyncio tasks and
into `asyncio.to_thread` workers, so the id follows the turn automatically.
Outside a turn the id is "-".
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

current_user_transcription: ContextVar[str | None] = ContextVar(
    "current_user_transcription",
    default=None,
)

NO_CORRELATION_ID = "-"

correlation_id: ContextVar[str] = ContextVar(
    "correlation_id",
    default=NO_CORRELATION_ID,
)


# Monotonic start time of the current voice turn (None outside a turn).
# Lets any stage (STT, LLM, TTS thread) log "+N ms since the turn started"
# without passing timestamps through every call signature.
turn_started_at: ContextVar[float | None] = ContextVar(
    "turn_started_at",
    default=None,
)


def ms_since_turn_start() -> int | None:
    """Milliseconds since the current turn started, or None outside a turn."""
    started = turn_started_at.get()
    if started is None:
        return None
    return int((time.monotonic() - started) * 1000)


def new_correlation_id() -> str:
    """Short random id for one voice turn (8 hex chars: readable, unique enough)."""
    return uuid.uuid4().hex[:8]


class CorrelationIdFilter(logging.Filter):
    """Adds `record.correlation_id` so formatters can use %(correlation_id)s.

    Attach it to HANDLERS, not loggers: logger-level filters do not run for
    records propagated from child loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = correlation_id.get()
        return True
