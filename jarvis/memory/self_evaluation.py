"""Self-evaluation ("auto-critic") — Layer 1: trace collection.

OPT-IN, OFF BY DEFAULT. When the user enables it in Settings and supplies
an external critic API key (see SelfEvaluationConfig), Jarvis periodically
sends recent interaction traces to an external critic LLM that grades how
well each request was handled. This module is Layer 1: it builds the
traces and strips secrets. The background task, the API call, and the
report/UI are later layers.

A trace is the full path of one interaction:

    user utterance (what was said)
      -> recognised text (STT output, if different)
      -> steps: each chosen tool + its args + the result
      -> final spoken answer

Privacy
-------
The user chose "full traces, minus secrets". ``strip_secrets`` removes
anything key/password/login-shaped from every string that leaves the
machine — API keys, bearer tokens, passwords in "password: ..." form,
and values of args named like credentials. This runs before a trace is
ever handed to the (later-layer) API sender.

Nothing here is Windows-specific and nothing calls the network. Pure
Python + dataclasses, fully unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Secret stripping
# ---------------------------------------------------------------------------

_REDACTED = "[REDACTED]"

# Arg names whose VALUES are credentials and must never be sent.
_SECRET_ARG_NAMES: frozenset[str] = frozenset({
    "password", "passwd", "pwd", "pass",
    "api_key", "apikey", "api-key", "key",
    "token", "access_token", "auth_token", "bearer",
    "secret", "client_secret", "login", "username", "user",
    "credential", "credentials", "otp", "pin", "passphrase",
})

# Value-shaped secrets found inside free text.
_SECRET_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key: value / password = value  (label followed by a token)
    re.compile(
        r"(?i)\b(password|passwd|pwd|api[_-]?key|token|secret|login|"
        r"username|bearer|passphrase|otp|pin)\b\s*[:=]\s*\S+"
    ),
    # Bearer <token>
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    # Common key prefixes: sk-..., gsk_..., ghp_..., xoxb-...
    re.compile(r"\b(?:sk|gsk|ghp|gho|xox[bap])[-_][A-Za-z0-9]{8,}\b"),
    # Long opaque tokens (>=24 chars of base64-ish) — conservative.
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
)


def strip_secrets(text: str) -> str:
    """Redact key/password/login-shaped substrings from *text*.

    Conservative by design: it is fine to over-redact an opaque blob, but
    a leaked credential is unacceptable. Never raises.
    """
    if not text:
        return text
    out = text
    for pat in _SECRET_TEXT_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


def strip_secret_args(args: dict) -> dict:
    """Return a copy of *args* with credential-named values redacted and
    every remaining string value run through ``strip_secrets``."""
    clean: dict = {}
    for k, v in (args or {}).items():
        if str(k).lower() in _SECRET_ARG_NAMES:
            clean[k] = _REDACTED
        elif isinstance(v, str):
            clean[k] = strip_secrets(v)
        else:
            clean[k] = v
    return clean


# ---------------------------------------------------------------------------
# Trace model
# ---------------------------------------------------------------------------

@dataclass
class TraceStep:
    """One tool invocation inside an interaction."""

    tool_name: str
    args: dict = field(default_factory=dict)
    result: str = ""
    success: bool = True

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class InteractionTrace:
    """The full path of a single interaction, ready for the critic.

    correlation_id ties it back to the logs it was assembled from.
    """

    correlation_id: str
    utterance: str
    recognised_text: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    final_answer: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "correlation_id": self.correlation_id,
            "utterance": self.utterance,
            "recognised_text": self.recognised_text,
            "steps": [s.to_dict() for s in self.steps],
            "final_answer": self.final_answer,
            "timestamp": self.timestamp,
        }


def build_trace(
    *,
    correlation_id: str,
    utterance: str,
    recognised_text: str = "",
    steps: list[TraceStep] | None = None,
    final_answer: str = "",
    timestamp: str = "",
) -> InteractionTrace:
    """Assemble one InteractionTrace with secrets stripped from every
    user-facing string and every tool argument.

    This is the single choke point: anything that becomes part of a trace
    passes through secret-stripping here, so no later layer has to
    remember to do it.
    """
    clean_steps: list[TraceStep] = []
    for st in (steps or []):
        clean_steps.append(
            TraceStep(
                tool_name=st.tool_name,
                args=strip_secret_args(st.args),
                result=strip_secrets(st.result),
                success=st.success,
            )
        )
    return InteractionTrace(
        correlation_id=correlation_id,
        utterance=strip_secrets(utterance),
        recognised_text=strip_secrets(recognised_text),
        steps=clean_steps,
        final_answer=strip_secrets(final_answer),
        timestamp=timestamp,
    )
