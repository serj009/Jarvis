"""Tests for Security Layer 1: Cryptographic Origin Tracker.

Covers HMAC signing/verification, tamper detection, token expiry,
RequestContext creation, and ContextVar propagation.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from jarvis.security.origin_tracker import (
    _MAX_TOKEN_AGE_S,
    OriginTracker,
    RequestOrigin,
    get_request_context,
    set_request_context,
)

# ---------------------------------------------------------------------------
# OriginTracker — signing & verification
# ---------------------------------------------------------------------------


class TestOriginTrackerSignVerify:
    """HMAC-based signing and verification of user input."""

    def test_sign_returns_nonempty_token(self) -> None:
        tracker = OriginTracker()
        token = tracker.sign_user_input("open youtube")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_valid_token(self) -> None:
        tracker = OriginTracker()
        text = "search for cats"
        token = tracker.sign_user_input(text)
        assert tracker.verify_token(text, token) is True

    def test_verify_different_text_fails(self) -> None:
        tracker = OriginTracker()
        token = tracker.sign_user_input("open youtube")
        assert tracker.verify_token("open spotify", token) is False

    def test_verify_tampered_signature_fails(self) -> None:
        tracker = OriginTracker()
        token = tracker.sign_user_input("test")
        # Flip a char in the hex signature portion
        ts, sig = token.split(":", 1)
        tampered_char = "0" if sig[-1] != "0" else "1"
        tampered = f"{ts}:{sig[:-1]}{tampered_char}"
        assert tracker.verify_token("test", tampered) is False

    def test_verify_tampered_timestamp_fails(self) -> None:
        tracker = OriginTracker()
        token = tracker.sign_user_input("test")
        ts, sig = token.split(":", 1)
        new_ts = str(float(ts) + 1)
        tampered = f"{new_ts}:{sig}"
        assert tracker.verify_token("test", tampered) is False

    def test_verify_empty_token_fails(self) -> None:
        tracker = OriginTracker()
        assert tracker.verify_token("text", "") is False

    def test_verify_malformed_token_fails(self) -> None:
        tracker = OriginTracker()
        assert tracker.verify_token("text", "not-a-valid-token") is False

    def test_verify_no_colon_fails(self) -> None:
        tracker = OriginTracker()
        assert tracker.verify_token("text", "abc123") is False

    def test_verify_non_numeric_timestamp_fails(self) -> None:
        tracker = OriginTracker()
        assert tracker.verify_token("text", "notanumber:abcdef") is False

    def test_different_sessions_different_keys(self) -> None:
        """Two OriginTracker instances (= two sessions) have different keys,
        so a token from one cannot verify against the other."""
        t1 = OriginTracker()
        t2 = OriginTracker()
        text = "open chrome"
        token = t1.sign_user_input(text)
        assert t1.verify_token(text, token) is True
        assert t2.verify_token(text, token) is False

    def test_token_format(self) -> None:
        """Token is '<timestamp>:<hex>' with a 64-char SHA-256 hex digest."""
        tracker = OriginTracker()
        token = tracker.sign_user_input("hello")
        parts = token.split(":", 1)
        assert len(parts) == 2
        ts_str, sig = parts
        float(ts_str)  # must parse as float
        assert len(sig) == 64  # SHA-256 hex = 64 chars

    def test_unicode_text_signing(self) -> None:
        """HMAC correctly handles unicode (Russian, Ukrainian, emoji)."""
        tracker = OriginTracker()
        for text in ["открой ютуб", "відкрий хром", "🎵 play music"]:
            token = tracker.sign_user_input(text)
            assert tracker.verify_token(text, token) is True


# ---------------------------------------------------------------------------
# Token expiry
# ---------------------------------------------------------------------------


class TestTokenExpiry:
    """Tokens older than _MAX_TOKEN_AGE_S must be rejected."""

    def test_expired_token_rejected(self) -> None:
        tracker = OriginTracker()
        text = "test"
        token = tracker.sign_user_input(text)
        # Simulate time passing beyond the max age
        with patch("jarvis.security.origin_tracker.time") as mock_time:
            mock_time.time.return_value = time.time() + _MAX_TOKEN_AGE_S + 10
            assert tracker.verify_token(text, token) is False

    def test_future_timestamp_rejected(self) -> None:
        """A token with a timestamp in the future (age < 0) is suspicious."""
        tracker = OriginTracker()
        # Craft a token with a future timestamp
        future_ts = str(time.time() + 3600)
        sig = tracker._hmac("test", future_ts)
        forged = f"{future_ts}:{sig}"
        assert tracker.verify_token("test", forged) is False


# ---------------------------------------------------------------------------
# RequestContext
# ---------------------------------------------------------------------------


class TestRequestContext:
    """RequestContext creation and is_user_initiated property."""

    def test_create_user_voice_context(self) -> None:
        tracker = OriginTracker()
        ctx = tracker.create_context(
            RequestOrigin.USER_VOICE,
            original_text="hello",
            token="fake-token",
        )
        assert ctx.origin is RequestOrigin.USER_VOICE
        assert ctx.is_user_initiated is True
        assert ctx.original_text == "hello"
        assert ctx.token == "fake-token"

    def test_create_user_pattern_context(self) -> None:
        tracker = OriginTracker()
        ctx = tracker.create_context(
            RequestOrigin.USER_PATTERN,
            original_text="open chrome",
        )
        assert ctx.is_user_initiated is True

    def test_create_llm_context(self) -> None:
        tracker = OriginTracker()
        ctx = tracker.create_context(
            RequestOrigin.LLM_TOOL_CALL,
            original_text="",
        )
        assert ctx.is_user_initiated is False

    def test_timestamp_is_recent(self) -> None:
        tracker = OriginTracker()
        before = time.time()
        ctx = tracker.create_context(RequestOrigin.LLM_TOOL_CALL)
        after = time.time()
        assert before <= ctx.timestamp <= after


# ---------------------------------------------------------------------------
# ContextVar propagation
# ---------------------------------------------------------------------------


class TestContextVar:
    """ContextVar get/set helpers."""

    def test_default_is_llm_tool_call(self) -> None:
        """When no context has been set, fail-closed to LLM_TOOL_CALL."""
        set_request_context(None)  # reset
        ctx = get_request_context()
        assert ctx.origin is RequestOrigin.LLM_TOOL_CALL
        assert ctx.is_user_initiated is False

    def test_set_and_get(self) -> None:
        tracker = OriginTracker()
        ctx = tracker.create_context(
            RequestOrigin.USER_VOICE,
            original_text="test",
            token="tok",
        )
        set_request_context(ctx)
        retrieved = get_request_context()
        assert retrieved.origin is RequestOrigin.USER_VOICE
        assert retrieved.original_text == "test"
        set_request_context(None)  # cleanup

    def test_set_none_reverts_to_default(self) -> None:
        tracker = OriginTracker()
        ctx = tracker.create_context(RequestOrigin.USER_VOICE)
        set_request_context(ctx)
        set_request_context(None)
        default = get_request_context()
        assert default.origin is RequestOrigin.LLM_TOOL_CALL
