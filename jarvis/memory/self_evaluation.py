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

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Layer 2: critic prompt, evaluation runner, report
# ---------------------------------------------------------------------------

import json as _json
import logging as _logging

_log = _logging.getLogger(__name__)

_CRITIC_SYSTEM_PROMPT = (
    "You are a strict but fair QA critic for a local voice assistant named "
    "Jarvis. You are given traces of real interactions: what the user said, "
    "what speech-to-text produced, which tools Jarvis chose, their results, "
    "and the final spoken answer. For EACH trace, judge how well Jarvis "
    "handled the request. Be concrete and actionable. Do NOT invent facts "
    "not present in the trace. Respond with STRICT JSON only, no prose, "
    "matching exactly this shape:\n"
    '{"evaluations": [{"correlation_id": "<id>", "score": <0-10 int>, '
    '"verdict": "<one short sentence>", '
    '"issues": ["<issue>", ...], '
    '"suggestions": ["<actionable suggestion>", ...]}], '
    '"overall": "<one-paragraph summary across all traces>"}'
)


def build_critic_messages(traces: list[InteractionTrace]) -> list[dict]:
    """Assemble the chat messages sent to the critic.

    Traces are already secret-stripped (build_trace guarantees it). The
    payload is compact JSON so it fits free-tier token budgets.
    """
    payload = {"traces": [t.to_dict() for t in traces]}
    user_content = (
        "Evaluate these interaction traces and return the JSON described in "
        "the system prompt.\n\n" + _json.dumps(payload, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": _CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_critic_response(raw: str) -> dict:
    """Parse the critic's reply into a report dict.

    Tolerant: critics sometimes wrap JSON in ``` fences or add stray text.
    Extracts the first ``{...}`` block. On total failure returns a
    well-formed error report rather than raising — a bad critic reply must
    never crash the background task.
    """
    if not raw or not raw.strip():
        return {"evaluations": [], "overall": "", "error": "empty critic response"}
    text = raw.strip()
    # Strip markdown fences if present.
    if "```" in text:
        import re as _re
        m = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
        if m:
            text = m.group(1).strip()
    # Fall back to the first balanced-looking {...} span.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        data = _json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("critic JSON is not an object")
        data.setdefault("evaluations", [])
        data.setdefault("overall", "")
        return data
    except Exception as exc:
        _log.warning("could not parse critic response: %s", exc)
        return {
            "evaluations": [],
            "overall": "",
            "error": f"unparseable critic response: {exc}",
            "raw": raw[:500],
        }


async def run_evaluation(
    traces: list[InteractionTrace],
    send_fn,
    *,
    max_traces: int = 20,
) -> dict:
    """Run one evaluation pass. Returns a report dict.

    ``send_fn`` is an async callable ``(messages: list[dict]) -> str`` that
    performs the actual API round-trip and returns the critic's raw text.
    Injecting it keeps this function network-free and unit-testable, and
    lets the app wire in APILLMClient (or any provider) without this module
    importing the LLM stack.

    Empty input or a failing ``send_fn`` yields a well-formed report with an
    ``error`` field — never an exception.
    """
    if not traces:
        return {"evaluations": [], "overall": "", "error": "no traces to evaluate"}

    batch = traces[:max_traces]
    messages = build_critic_messages(batch)
    try:
        raw = await send_fn(messages)
    except Exception as exc:
        _log.warning("critic send_fn failed: %s", exc)
        return {
            "evaluations": [],
            "overall": "",
            "error": f"critic call failed: {exc}",
        }
    report = parse_critic_response(raw)
    report["trace_count"] = len(batch)
    return report


def render_report_markdown(report: dict, *, generated_at: str = "") -> str:
    """Render an evaluation report as human-readable Markdown for the UI /
    a saved file. Pure formatting; never raises on a partial report."""
    lines: list[str] = ["# Jarvis self-evaluation report"]
    if generated_at:
        lines.append(f"_Generated: {generated_at}_")
    if report.get("error"):
        lines.append("")
        lines.append(f"> ⚠️ {report['error']}")
    evals = report.get("evaluations") or []
    lines.append("")
    lines.append(f"**Traces evaluated:** {report.get('trace_count', len(evals))}")
    if report.get("overall"):
        lines.append("")
        lines.append("## Overall")
        lines.append(str(report["overall"]))
    if evals:
        lines.append("")
        lines.append("## Per-interaction")
        for ev in evals:
            cid = ev.get("correlation_id", "?")
            score = ev.get("score", "?")
            verdict = ev.get("verdict", "")
            lines.append("")
            lines.append(f"### `{cid}` — score {score}/10")
            if verdict:
                lines.append(verdict)
            issues = ev.get("issues") or []
            if issues:
                lines.append("")
                lines.append("**Issues:**")
                lines.extend(f"- {i}" for i in issues)
            suggestions = ev.get("suggestions") or []
            if suggestions:
                lines.append("")
                lines.append("**Suggestions:**")
                lines.extend(f"- {sug}" for sug in suggestions)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Layer 2: background scheduler
# ---------------------------------------------------------------------------

import asyncio as _asyncio
from datetime import UTC as _UTC, datetime as _dt
from pathlib import Path as _Path


class SelfEvaluationScheduler:
    """Runs the critic periodically in the background. OFF unless enabled.

    Fully decoupled for testability and safety:
      - ``trace_source()``  -> list[InteractionTrace]  (already stripped)
      - ``send_fn(messages)`` -> raw critic text        (the API round-trip)
      - ``report_dir``       -> where markdown reports are written

    The loop never raises out: any failure is logged and the loop waits for
    the next interval. A disabled scheduler's ``start()`` is a no-op, so the
    local-only default path pays nothing.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        interval_hours: float,
        max_traces_per_run: int,
        trace_source,
        send_fn,
        report_dir: _Path,
    ) -> None:
        self._enabled = enabled
        self._interval_s = max(0.5, float(interval_hours)) * 3600.0
        self._max_traces = max_traces_per_run
        self._trace_source = trace_source
        self._send_fn = send_fn
        self._report_dir = _Path(report_dir)
        self._task: _asyncio.Task | None = None
        self._stop = _asyncio.Event()
        self.last_report: dict | None = None
        self.last_report_path: _Path | None = None

    def start(self) -> None:
        """Launch the background loop if enabled. Idempotent."""
        if not self._enabled:
            _log.info("[self-eval] disabled; not starting")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = _asyncio.create_task(self._loop(), name="self-evaluation")
        _log.info(
            "[self-eval] scheduler started (every %.1f h)",
            self._interval_s / 3600.0,
        )

    async def stop(self) -> None:
        """Signal the loop to exit and await it."""
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:
                log.debug("self-evaluation task ended with error",
                          exc_info=True)
            self._task = None

    async def _loop(self) -> None:
        # First run after one interval, not immediately at boot, so startup
        # stays cheap and there is something to evaluate.
        while not self._stop.is_set():
            try:
                await _asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop was signalled
            except TimeoutError:
                pass  # interval elapsed -> run
            try:
                await self.run_once()
            except Exception:
                _log.warning("[self-eval] run failed (non-fatal)", exc_info=True)

    async def run_once(self) -> dict:
        """Collect traces, evaluate, persist the report. Returns the report."""
        traces = list(self._trace_source() or [])
        report = await run_evaluation(
            traces, self._send_fn, max_traces=self._max_traces,
        )
        self.last_report = report
        now = _dt.now(_UTC)
        md = render_report_markdown(report, generated_at=now.isoformat())
        try:
            self._report_dir.mkdir(parents=True, exist_ok=True)
            path = self._report_dir / f"self_eval_{now:%Y%m%d_%H%M%S}.md"
            path.write_text(md, encoding="utf-8")
            self.last_report_path = path
            _log.info("[self-eval] report written: %s", path)
        except Exception:
            _log.warning("[self-eval] could not write report", exc_info=True)
        return report


# ---------------------------------------------------------------------------
# Layer 3: live trace recorder (bus observer) + send_fn factory
# ---------------------------------------------------------------------------

from collections import deque as _deque


class TraceRecorder:
    """Assembles interaction traces by observing the event bus.

    Pure observer: it subscribes to events the pipeline ALREADY publishes
    (TranscriptionReady, ToolInvoked, ToolResult, LLMResponseComplete) and
    groups them per turn. A new TranscriptionReady starts a new trace; tool
    and LLM events attach to the in-progress trace until the next one. No
    hot-path code changes, no correlation-id plumbing.

    Bounded: keeps at most ``maxlen`` finished traces (a ring buffer), so a
    long-running session cannot grow memory. Every trace is secret-stripped
    via build_trace when finalised.

    Wiring (in app.py, only when self_evaluation.enabled):
        rec = TraceRecorder()
        bus.subscribe(TranscriptionReady, rec.on_transcription)
        bus.subscribe(ToolInvoked, rec.on_tool_invoked)
        bus.subscribe(ToolResult, rec.on_tool_result)
        bus.subscribe(LLMResponseComplete, rec.on_llm_complete)
        scheduler = SelfEvaluationScheduler(..., trace_source=rec.drain, ...)
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._finished: _deque = _deque(maxlen=maxlen)
        self._cur_utterance: str | None = None
        self._cur_steps: list[TraceStep] = []
        self._cur_final: str = ""
        self._seq = 0

    # -- bus handlers (each takes the event object) ------------------------

    def on_transcription(self, event) -> None:
        # A new user turn begins; finalise the previous one first.
        self._flush()
        self._cur_utterance = getattr(event, "text", "") or ""
        self._cur_steps = []
        self._cur_final = ""

    def on_tool_invoked(self, event) -> None:
        if self._cur_utterance is None:
            return
        self._cur_steps.append(
            TraceStep(
                tool_name=getattr(event, "tool_name", "") or "",
                args=dict(getattr(event, "args", {}) or {}),
                result="",
                success=True,
            )
        )

    def on_tool_result(self, event) -> None:
        if self._cur_utterance is None or not self._cur_steps:
            return
        # Attach to the most recent step for the same tool.
        name = getattr(event, "tool_name", "") or ""
        err = getattr(event, "error", None)
        res = getattr(event, "result", None)
        for step in reversed(self._cur_steps):
            if step.tool_name == name:
                step.result = str(res) if res is not None else (err or "")
                step.success = err is None
                break

    def on_llm_complete(self, event) -> None:
        if self._cur_utterance is None:
            return
        self._cur_final = getattr(event, "full_text", "") or ""

    # -- trace access ------------------------------------------------------

    def _flush(self) -> None:
        """Finalise the in-progress turn into a secret-stripped trace."""
        if self._cur_utterance is None:
            return
        if not self._cur_utterance and not self._cur_steps and not self._cur_final:
            self._cur_utterance = None
            return
        self._seq += 1
        trace = build_trace(
            correlation_id=f"turn-{self._seq}",
            utterance=self._cur_utterance,
            recognised_text=self._cur_utterance,
            steps=self._cur_steps,
            final_answer=self._cur_final,
            timestamp=_dt.now(_UTC).isoformat(),
        )
        self._finished.append(trace)
        self._cur_utterance = None
        self._cur_steps = []
        self._cur_final = ""

    def drain(self) -> list[InteractionTrace]:
        """Return all finished traces and clear the buffer.

        Used as the scheduler's ``trace_source``: each evaluation run
        consumes the traces collected since the last run.
        """
        self._flush()
        out = list(self._finished)
        self._finished.clear()
        return out


def make_send_fn(cfg):
    """Build an async ``send_fn(messages) -> str`` from a SelfEvaluationConfig.

    Lazily constructs an APILLMClient-style provider so this module never
    imports the LLM stack at import time. Returns None when the config is
    not usable (disabled or no key), so the caller can skip wiring.
    """
    if not getattr(cfg, "enabled", False):
        return None
    if not getattr(cfg, "api_key", ""):
        _log.warning("[self-eval] enabled but no API key; critic disabled")
        return None

    async def send_fn(messages: list[dict]) -> str:
        # Build a throwaway LLM client mapped to the critic settings. The
        # existing APILLMClient.from_config reads provider/api_model/api_key/
        # api_base_url off any object with those attributes.
        from types import SimpleNamespace

        from jarvis.llm.llm_provider import APILLMClient

        provider = cfg.provider
        # openai_compatible routes through the openai path with a base_url.
        llm_shim = SimpleNamespace(
            provider="openai" if provider == "openai_compatible" else provider,
            api_model=cfg.api_model,
            api_key=cfg.api_key,
            api_base_url=cfg.api_base_url,
            temperature=0.2,  # critic should be steady, not creative
            max_tokens=2048,
            system_prompt="",
        )
        client = APILLMClient.from_config(llm_shim, None)
        await client.load()
        parts: list[str] = []
        async for chunk in client.stream_chat(messages):
            if getattr(chunk, "content", ""):
                parts.append(chunk.content)
        return "".join(parts)

    return send_fn
