"""Unified error hierarchy and resilient error handler (roadmap T1.5).

Design notes:

- Four domain-specific error classes (OllamaError already exists in
  llm/ollama_client.py — we do NOT duplicate it here; we import and re-export
  so callers can use ``from jarvis.core.errors import OllamaError``).
- Each error carries a user-facing ``spoken_key`` that maps to an i18n phrase
  in ``core.phrases``.  The ErrorHandler looks up the spoken phrase via
  ``phrases.PHRASES[spoken_key]`` and returns it for TTS in the user's
  language — the voice loop NEVER reads raw tracebacks to the user.
- ErrorHandler wraps arbitrary callables (sync or async) and guarantees
  the voice loop survives: log → count → speak → continue.  A consecutive
  error counter triggers a "degraded mode" notice after N errors, but
  still does not crash.
- Thread safety: the handler is instantiated once and called from the
  asyncio audio loop.  The consecutive counter is only touched from that
  loop, so no lock is needed.

Integration:
    from jarvis.core.errors import ErrorHandler, TTSError, STTError, VRAMError
    handler = ErrorHandler(bus=bus)
    result = await handler.safe_call(some_coro(), stage="tts")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

from jarvis.core.phrases import PHRASES, reply_language

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from jarvis.core.events import EventBus

log = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class JarvisError(RuntimeError):
    """Base for all Jarvis-specific runtime errors.

    Attributes:
        spoken_key: key into ``phrases.PHRASES`` for the user-facing
                    spoken message (e.g. ``"error_tts"``).
    """

    spoken_key: str = "error_generic"

    def __init__(self, message: str = "", *, spoken_key: str | None = None) -> None:
        super().__init__(message)
        if spoken_key is not None:
            self.spoken_key = spoken_key


class STTError(JarvisError):
    """Speech-to-text failure (model load, decode, timeout)."""

    spoken_key: str = "error_stt"


class TTSError(JarvisError):
    """Text-to-speech failure (model load, synthesis, audio device)."""

    spoken_key: str = "error_tts"


class VRAMError(JarvisError):
    """GPU memory allocation or eviction failure."""

    spoken_key: str = "error_vram"


# Re-export OllamaError so callers can import everything from one place.
# OllamaError predates this module and lives in llm/ollama_client.py.
from jarvis.llm.ollama_client import OllamaError  # noqa: E402


# Error phrases (error_generic, error_stt, error_tts, error_vram,
# error_ollama, error_degraded) are defined directly in core.phrases.PHRASES
# so they are available regardless of import order. No merge needed here.


# ---------------------------------------------------------------------------
# ErrorHandler
# ---------------------------------------------------------------------------

# After this many consecutive errors (any stage) we publish a degraded notice.
_DEGRADED_THRESHOLD = 5


class ErrorHandler:
    """Resilient wrapper that ensures the voice loop never crashes.

    Usage::

        handler = ErrorHandler(bus=bus)

        # In the voice loop:
        result = await handler.safe_call(stt_coro(), stage="stt")
        if result is handler.FAILED:
            continue  # skip this turn

    The handler:
    1. Catches any exception.
    2. Logs it with full traceback.
    3. Publishes a NonFatalError event on the bus.
    4. Increments a consecutive-error counter.
    5. Returns ``FAILED`` sentinel so the caller can skip gracefully.
    6. After ``_DEGRADED_THRESHOLD`` consecutive errors, emits a degraded
       notice (once) — resets when a call succeeds.
    """

    class _FailedSentinel:
        """Unique sentinel; identity-compare with ``is handler.FAILED``."""

        def __repr__(self) -> str:
            return "<FAILED>"

    FAILED = _FailedSentinel()

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus
        self._consecutive_errors = 0
        self._degraded_notified = False

    def reset(self) -> None:
        """Call after a successful operation to clear the error counter."""
        if self._consecutive_errors > 0:
            log.debug(
                "error counter reset (was %d consecutive)", self._consecutive_errors
            )
        self._consecutive_errors = 0
        self._degraded_notified = False

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    async def safe_call(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        stage: str = "unknown",
    ) -> T | _FailedSentinel:
        """Await *coro*; on any exception return ``FAILED`` instead of raising.

        Parameters
        ----------
        coro:
            The awaitable to execute (e.g. ``stt.transcribe(audio)``).
        stage:
            Human-readable pipeline stage name for logging and events
            (e.g. ``"stt"``, ``"llm"``, ``"tts"``).
        """
        try:
            result = await coro
            self.reset()
            return result
        except Exception as exc:
            self._consecutive_errors += 1
            spoken_key = getattr(exc, "spoken_key", "error_generic")

            log.exception(
                "[%s] error #%d (spoken_key=%s): %s",
                stage,
                self._consecutive_errors,
                spoken_key,
                exc,
            )

            self._publish_error(stage, exc, spoken_key)

            if (
                self._consecutive_errors >= _DEGRADED_THRESHOLD
                and not self._degraded_notified
            ):
                self._degraded_notified = True
                log.warning(
                    "degraded: %d consecutive errors", self._consecutive_errors
                )
                self._publish_error(stage, exc, "error_degraded")

            return self.FAILED

    def spoken_message(self, spoken_key: str) -> str:
        """Return the i18n phrase for *spoken_key* in the current reply language."""
        lang = reply_language()
        phrases = PHRASES.get(spoken_key, PHRASES["error_generic"])
        return phrases.get(lang, phrases.get("en", "Something went wrong, sir."))

    # -- internal helpers ---------------------------------------------------

    def _publish_error(self, stage: str, exc: Exception, spoken_key: str) -> None:
        if self._bus is None:
            return
        from jarvis.core.events import NonFatalError

        self._bus.publish(
            NonFatalError(
                module=stage,
                issue=type(exc).__name__,
                expected="normal operation",
                actual=str(exc)[:200],
            )
        )
