"""Structured logging utilities (roadmap T1.2).

Provides three components that complement the existing ``correlation_id``
and ``CorrelationIdFilter`` in ``core.request_context``:

1. **JsonFormatter** — outputs each log record as a single JSON line.
   Fields: ``ts``, ``level``, ``logger``, ``correlation_id``,
   ``turn_ms``, ``msg``, ``exc`` (optional).  Machine-parseable for
   ``jq`` / ELK / Loki while still human-readable with ``| python -m json.tool``.

2. **@log_latency** — decorator for async functions that logs elapsed time
   at DEBUG level with the function name and correlation_id.  Use on
   pipeline stages (STT, LLM, TTS) so latency is visible per-turn::

       @log_latency
       async def transcribe(self, audio: bytes) -> str: ...

3. **PipelineTracker** — lightweight stopwatch that accumulates per-stage
   durations within one voice turn, then emits a single summary line::

       tracker = PipelineTracker()
       tracker.start("stt")
       ...
       tracker.stop("stt")
       tracker.start("llm")
       ...
       tracker.stop("llm")
       tracker.log_summary()
       # => "pipeline: stt=112ms llm=340ms tts=85ms total=537ms [abc12345]"

All three respect ``correlation_id`` from request_context — no extra
wiring needed.

Integration (in app.py or wherever logging is configured):

    import logging
    from jarvis.core.logging_utils import JsonFormatter
    from jarvis.core.request_context import CorrelationIdFilter

    handler = logging.FileHandler("jarvis.log.jsonl")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())
    logging.root.addHandler(handler)
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from jarvis.core.request_context import correlation_id, ms_since_turn_start

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# 1. JsonFormatter
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields:
        ts              ISO-8601 timestamp (UTC)
        level           DEBUG / INFO / WARNING / ERROR / CRITICAL
        logger          logger name
        correlation_id  per-turn id (or "-" outside a turn)
        turn_ms         milliseconds since turn start (or null)
        msg             formatted message
        exc             exception text (only if present)
    """

    def format(self, record: logging.LogRecord) -> str:
        # CorrelationIdFilter should have set record.correlation_id, but
        # fall back gracefully if it hasn't been attached.
        cid = getattr(record, "correlation_id", correlation_id.get())
        turn_ms = ms_since_turn_start()

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": cid,
            "turn_ms": turn_ms,
            "msg": record.getMessage(),
        }

        if record.exc_info and record.exc_info[1] is not None:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 2. @log_latency decorator
# ---------------------------------------------------------------------------

def log_latency(fn: F) -> F:
    """Decorator that logs elapsed time of an async function at DEBUG level.

    Works with both ``async def`` and regular ``def``.  The correlation_id
    and turn_ms are picked up automatically from ContextVars.

    Example output::

        [abc12345] transcribe completed in 112 ms (+112ms since turn start)
    """
    if _is_coroutine_function(fn):

        @functools.wraps(fn)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            cid = correlation_id.get()
            t0 = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                return result
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                turn_ms = ms_since_turn_start()
                turn_info = f" (+{turn_ms}ms since turn start)" if turn_ms is not None else ""
                log.debug(
                    "[%s] %s completed in %.0f ms%s",
                    cid,
                    fn.__qualname__,
                    elapsed_ms,
                    turn_info,
                )

        return _async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        cid = correlation_id.get()
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.debug("[%s] %s completed in %.0f ms", cid, fn.__qualname__, elapsed_ms)

    return _sync_wrapper  # type: ignore[return-value]


def _is_coroutine_function(fn: Any) -> bool:
    """Check if fn is async, handling wrapped / partial functions."""
    import asyncio
    import inspect

    if asyncio.iscoroutinefunction(fn):
        return True
    if isinstance(fn, functools.partial):
        return asyncio.iscoroutinefunction(fn.func)
    wrapped = getattr(fn, "__wrapped__", None)
    return inspect.iscoroutinefunction(wrapped) if wrapped else False


# ---------------------------------------------------------------------------
# 3. PipelineTracker
# ---------------------------------------------------------------------------

class PipelineTracker:
    """Stopwatch for measuring per-stage latency within one voice turn.

    Usage::

        tracker = PipelineTracker()
        tracker.start("stt")
        text = await stt.transcribe(audio)
        tracker.stop("stt")

        tracker.start("llm")
        reply = await llm.chat(text)
        tracker.stop("llm")

        tracker.start("tts")
        audio = await tts.synthesize(reply)
        tracker.stop("tts")

        tracker.log_summary()
        # INFO: pipeline: stt=112ms llm=340ms tts=85ms total=537ms [abc12345]
    """

    def __init__(self) -> None:
        self._stages: dict[str, float] = {}  # stage -> elapsed_ms
        self._running: dict[str, float] = {}  # stage -> start_time
        self._order: list[str] = []  # insertion order

    def start(self, stage: str) -> None:
        """Begin timing *stage*."""
        self._running[stage] = time.perf_counter()
        if stage not in self._stages:
            self._order.append(stage)

    def stop(self, stage: str) -> float:
        """Stop timing *stage*.  Returns elapsed milliseconds.

        Raises KeyError if *stage* was not started.
        """
        t0 = self._running.pop(stage, None)
        if t0 is None:
            raise KeyError(f"stage {stage!r} was not started")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._stages[stage] = self._stages.get(stage, 0.0) + elapsed_ms
        return elapsed_ms

    @property
    def total_ms(self) -> float:
        return sum(self._stages.values())

    def summary(self) -> str:
        """Format a one-line summary string."""
        parts = [f"{s}={self._stages.get(s, 0):.0f}ms" for s in self._order]
        parts.append(f"total={self.total_ms:.0f}ms")
        cid = correlation_id.get()
        return f"pipeline: {' '.join(parts)} [{cid}]"

    def log_summary(self, level: int = logging.INFO) -> None:
        """Emit the summary via the module logger."""
        log.log(level, self.summary())

    def reset(self) -> None:
        """Clear all stages for reuse."""
        self._stages.clear()
        self._running.clear()
        self._order.clear()

    def as_dict(self) -> dict[str, float]:
        """Return stage durations as a dict (for JSON logging)."""
        return dict(self._stages)
