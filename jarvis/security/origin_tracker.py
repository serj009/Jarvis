"""Cryptographic origin tracking for tool calls (Security Layer 1).

Uses HMAC signatures to cryptographically prove that a tool call
originated from real user input (voice/keyboard), not from the LLM
generating a fake "user said" instruction.

Session key is generated at startup (random 32 bytes).
When the audio pipeline receives STT text, it signs it:
    token = HMAC(session_key, utterance_text + timestamp)

When IntentRouter processes the text:
    - VoicePattern match → origin = USER_PATTERN (inherits token)
    - LLM tool_call → origin = LLM_TOOL_CALL (no token)

When a network-capable tool is about to execute:
    - Has valid token → ALLOWED (user requested it)
    - No token → checked by DataLeakScanner (Layer 2)

Threading model:
    RequestContext is propagated via ``contextvars.ContextVar``, which
    the existing codebase already uses for ``correlation_id`` and
    ``turn_started_at`` (see ``jarvis.core.request_context``).  The
    audio pipeline sets the ContextVar once per voice turn after STT;
    it is automatically visible to the IntentRouter and every tool
    executed within that turn's async task.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Origin classification
# ---------------------------------------------------------------------------

class RequestOrigin(Enum):
    """Where a tool call came from."""

    USER_VOICE = "user_voice"       # User said it (STT transcription)
    USER_PATTERN = "user_pattern"   # VoicePattern regex match (deterministic)
    LLM_TOOL_CALL = "llm_tool_call" # LLM decided to call the tool


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------

@dataclass
class RequestContext:
    """Immutable context that travels with every tool call.

    Created by the pipeline (USER_VOICE) or the router (USER_PATTERN /
    LLM_TOOL_CALL) and propagated via ContextVar.  The ToolGuard reads it
    to decide allow/block/warn.
    """

    origin: RequestOrigin
    token: str              # HMAC token — non-empty only for USER origins
    timestamp: float        # When the context was created (time.time)
    original_text: str      # The user's utterance (for audit logging)

    @property
    def is_user_initiated(self) -> bool:
        """True if cryptographically proven to come from user input."""
        return self.origin in (
            RequestOrigin.USER_VOICE,
            RequestOrigin.USER_PATTERN,
        )


# ---------------------------------------------------------------------------
# ContextVar for async propagation
# ---------------------------------------------------------------------------

_request_context_var: ContextVar[RequestContext | None] = ContextVar(
    "security_request_context",
    default=None,
)


def set_request_context(ctx: RequestContext | None) -> None:
    """Set the security context for the current async task."""
    _request_context_var.set(ctx)


def get_request_context() -> RequestContext:
    """Read the security context.

    Returns a restrictive LLM_TOOL_CALL default when no context has been
    set — fail-closed: an untracked call is treated as LLM-initiated so
    the leak scanner applies its strictest rules.
    """
    ctx = _request_context_var.get()
    if ctx is not None:
        return ctx
    return RequestContext(
        origin=RequestOrigin.LLM_TOOL_CALL,
        token="",
        timestamp=time.time(),
        original_text="",
    )


# ---------------------------------------------------------------------------
# HMAC-based origin tracker
# ---------------------------------------------------------------------------

# Token format: "<unix_timestamp>:<hex HMAC-SHA256>"
_TOKEN_SEP = ":"

# Maximum age of a valid token (seconds).  A voice turn on a local 7B
# model should never take this long; anything older is stale and rejected.
_MAX_TOKEN_AGE_S = 120.0


class OriginTracker:
    """Signs and verifies user input using per-session HMAC keys.

    A new random 32-byte key is generated every time JARVIS starts;
    tokens from a previous session are automatically invalid.  The LLM
    never sees the key — it lives only in the pipeline process.
    """

    def __init__(self) -> None:
        self._session_key: bytes = os.urandom(32)

    # -- public API -------------------------------------------------------

    def sign_user_input(self, text: str) -> str:
        """Sign a user utterance from the audio pipeline.

        Returns an opaque token string that can later be verified with
        ``verify_token``.
        """
        ts = f"{time.time():.6f}"
        sig = self._hmac(text, ts)
        return f"{ts}{_TOKEN_SEP}{sig}"

    def verify_token(self, text: str, token: str) -> bool:
        """Verify that *token* was produced by this session for *text*.

        Returns False for tampered, expired, or malformed tokens.
        """
        parts = token.split(_TOKEN_SEP, 1)
        if len(parts) != 2:
            return False
        ts_str, sig = parts

        # Timestamp validity
        try:
            ts = float(ts_str)
        except (ValueError, OverflowError):
            return False
        age = time.time() - ts
        # Allow a small negative age (-1 s) because sign_user_input
        # formats time.time() with :.6f — the rounding can push the
        # stored timestamp slightly above the true wall-clock value at
        # the moment of signing, making `time.time() - ts` negative by
        # up to a few microseconds.
        if age < -1 or age > _MAX_TOKEN_AGE_S:
            return False

        # HMAC comparison (constant-time)
        expected = self._hmac(text, ts_str)
        return hmac.compare_digest(sig, expected)

    def create_context(
        self,
        origin: RequestOrigin,
        original_text: str = "",
        token: str = "",
    ) -> RequestContext:
        """Build a ``RequestContext`` for the given origin."""
        return RequestContext(
            origin=origin,
            token=token,
            timestamp=time.time(),
            original_text=original_text,
        )

    # -- internals --------------------------------------------------------

    def _hmac(self, text: str, timestamp_str: str) -> str:
        payload = (text + timestamp_str).encode("utf-8")
        return hmac.new(
            self._session_key, payload, hashlib.sha256,
        ).hexdigest()
