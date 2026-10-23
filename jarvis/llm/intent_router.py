"""Hybrid intent router: pattern layer for snap commands, LLM for the rest.

Two layers
----------
1. Pattern layer (deterministic, fast, no LLM call). First-match-wins
   table of (regex, intent_builder) tuples applied to the user's
   transcription after filler-word stripping. Misses fall through to
   the LLM. SPEC: this is what makes Jarvis feel snappy -- "open
   spotify" should never wait for a 7B-parameter model.

2. LLM layer. The transcription is added to the conversation history,
   OllamaClient.stream_chat() is invoked, and the model's text is
   buffered for the duration of one round (see "Double-speaking"
   below). Tool calls arrive batched on the final chunk; the
   accumulation point below is defensive against Ollama later
   streaming them per-chunk.

Tool-result feedback loop
-------------------------
The standard OpenAI/Ollama round trip, so the model can act on what a
tool returned instead of dispatching blind:

  1. Model returns tool_calls.
  2. The router executes them through the registry.
  3. Conversation records ONE assistant message carrying those
     tool_calls plus one role:"tool" message per call, correlated by
     tool_call_id (Conversation.add_tool_round_trip).
  4. The model is re-invoked with that history.
  5. Repeat until it returns no tool calls, bounded by
     max_tool_iterations (default 3 -- latency on a local 7B is the
     binding constraint, not correctness).

Hitting the bound is not an error path that hangs or loops: the final
round's tool results are spoken directly, so the user always hears
something.

The loop needs a registry (you cannot observe a result you cannot
execute). Without one -- or with max_tool_iterations == 1 -- the
router falls back to the legacy one-shot behaviour: yield ToolIntents
and let the caller execute and speak them. That fallback is also the
config escape hatch for anyone who prefers a tool's own polished
output over the model's paraphrase of it.

Double-speaking
---------------
Text is buffered for the whole of each round rather than yielded per
chunk, because tool calls arrive last: speaking narration as it
streams and only then discovering a tool call would produce
"Opening Chrome..." followed by the tool's own "Opening Chrome". So a
round that ends in tool calls speaks nothing -- neither the narration
nor the raw tool result -- and the model's next round, which has seen
the results, is the single utterance the user hears. Only the
bound-exhausted path speaks a raw tool result, and only because there
is no further model round to summarise it.

Streaming contract with the pipeline
------------------------------------
route() is an async generator yielding Intent values. The pipeline
(Phase 4 wiring) collects consecutive SpeakIntents into a queue feeding
PiperTTS.speak_stream() so token-level chunks get sentence-segmented
inside TTS rather than firing one synthesis per token. ToolIntents
break the speak run cleanly (end-of-stream sentinel + speak_task
completion + tool execution). See the Phase 3 design note for the
dispatch pattern.

Cancellation
------------
The async generator propagates CancelledError up through
ollama_client.stream_chat() into the httpx stream's context manager
(verified by ollama_client tests). add_assistant_turn() is outside any
try/except, so a cancelled stream simply never reaches it -- partial
assistant text never enters conversation history.

The feedback loop preserves that property by construction. Every await
inside a round -- the stream, and each registry.execute() -- happens
BEFORE anything is written to history; the assistant tool_calls
message and its role:"tool" results are appended together in one
synchronous call with no await between them, so a cancel can land
before the round trip or after it, never inside it.

Deliberate tradeoffs (documented at point of choice)
----------------------------------------------------
- User turn IS preserved on cancellation. The user said it, that's
  fact. Two consecutive user turns with no assistant between is
  technically malformed history (most chat APIs assume strict
  alternation), but tracking "did this turn get a response?" adds
  state that's not worth Phase 3's complexity. Future-polish knob if
  the malformed shape causes observable model-quality issues.

- Tool execution is sequential after a SpeakIntent run completes.
  No mid-sentence tool firing in the current implementation: a stream
  of Speak("foo ") -> Speak("bar") -> Tool(...) drains the speak run
  fully before the tool fires. The architecture supports interleaving
  (ToolIntent breaks the speak run via the pipeline's queue sentinel)
  but the current router doesn't decompose responses to make use of
  it. Acceptable for v1; revisit if a UX win emerges from interleaving.
"""

from __future__ import annotations

import datetime
import json
import logging
import asyncio
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.core.phrases import reply_language, say
from jarvis.llm.conversation import ToolExchange

if TYPE_CHECKING:
    from jarvis.core.personalization import PersonalizationContext
    from jarvis.memory.store import MemoryStore
    from jarvis.memory.tool_examples import ToolExampleStore
    from jarvis.memory.relations import RelationStore
    from jarvis.memory.personal_aliases import PersonalAliasStore
    from jarvis.memory.user_profile import UserProfile
    from jarvis.memory.usage_stats import UsageStats
    from jarvis.core.game_mode import GameMode
    from jarvis.llm.conversation import Conversation
    from jarvis.llm.ollama_client import OllamaClient
    from jarvis.tools.registry import ToolRegistry, ToolResult
    from jarvis.skills.protocols import ProtocolEngine

log = logging.getLogger(__name__)

# Maximum LLM invocations per user turn. 3 buys the two-step
# interactions that matter ("check the weather, then act on it") while
# capping worst-case latency on a local 7B at three inferences. 1
# disables the feedback loop entirely and restores one-shot dispatch.
DEFAULT_MAX_TOOL_ITERATIONS = 3

# Cap on a single tool result fed back to the model. A directory
# listing or a web fetch can be tens of KB; a 7B's context is small and
# every extra token is latency on the NEXT inference too. Truncation is
# announced in-band so the model knows it is seeing a prefix rather
# than silently reasoning over half a listing.
MAX_TOOL_RESULT_CHARS = 4000
_TRUNCATION_NOTE = "\n... (result truncated)"

# Per-chunk read timeout for the streaming loop in _stream_round().
# If Ollama stops sending chunks for this many seconds the round is
# aborted and an error message is returned to the caller.  Distinct
# from PipelineConfig.llm_timeout_s which caps the entire LLM stage.
_STREAM_CHUNK_TIMEOUT_S: float = 60.0


# --- Intent types ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpeakIntent:
    """Text to be spoken. The router yields multiple of these for an
    LLM response (one per Ollama content chunk); the pipeline pumps
    them into PiperTTS.speak_stream which sentence-segments internally."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolIntent:
    """A tool to execute. spoken_response is an optional confirmation
    line (e.g. 'Done, sir.') the pipeline speaks after dispatch."""

    tool_name: str
    args: dict
    spoken_response: str | None = None


@dataclass(frozen=True, slots=True)
class CompoundIntent:
    """Group of intents from a single user utterance. The router itself
    does not currently emit this -- iterator-of-flat-intents handles
    'open spotify and tell me the weather' naturally via multiple
    yields. Reserved for SPEC compliance and future patterns that need
    explicit grouping (atomic dispatch semantics, etc.)."""

    intents: tuple


@dataclass(frozen=True, slots=True)
class StopIntent:
    """User wants Jarvis to stop talking without a verbal response.

    Emitted when the transcription matches _STOP_PATTERN ('stop',
    'shut up', 'nevermind', etc.). The pipeline adapter yields nothing
    for this intent so TTS never fires. The pipeline's zero-yield path
    (first_chunk_seen stays False) transitions ConversationalState to
    IDLE automatically. Neither a user turn nor an assistant turn is
    added to conversation history."""


Intent = SpeakIntent | ToolIntent | CompoundIntent | StopIntent

# The shape the Phase 4 pipeline will accept in place of the Phase 2
# string-based ResponseProducer.
IntentProducer = Callable[[str], AsyncIterator[Intent]]


# --- normalization ---------------------------------------------------

# Leading filler patterns. Stripped iteratively until no more match.
_FILLERS: tuple[re.Pattern, ...] = (
    # EN
    re.compile(r"^hey jarvis,?\s+"),
    re.compile(r"^(?:can|could|would) you (?:please )?"),
    re.compile(r"^please\s+"),
    # RU: "эй джарвис", "джарвис", "пожалуйста", "ну", "так", "эмм",
    #     "слушай", "можешь ли ты", "будь добр"
    re.compile(r"^(?:эй\s+)?(?:джарвис|жарвис),?\s+"),
    re.compile(r"^(?:можешь|можешь\s+ли\s+ты)\s+(?:пожалуйста\s+)?"),
    re.compile(r"^пожалуйста\s+"),
    re.compile(r"^будь\s+добр,?\s+"),
    re.compile(r"^(?:ну|так|ладно|слушай|значит|короче|это),?\s+"),
    re.compile(r"^(?:э[мм]+|а[мм]+|хм+),?\s+"),
    # UK: "гей джарвіс", "джарвіс", "будь ласка", "ну", "слухай",
    #     "чи можеш ти"
    re.compile(r"^(?:гей\s+)?(?:джарвіс|жарвіс),?\s+"),
    re.compile(r"^(?:чи\s+)?(?:можеш|зможеш)\s+(?:ти\s+)?(?:будь\s+ласка\s+)?"),
    re.compile(r"^будь\s+ласка,?\s+"),
    re.compile(r"^(?:ну|так|ладно|слухай|значить|коротше|це),?\s+"),
    re.compile(r"^(?:е[мм]+|гм+),?\s+"),
    # RU/UK extra fillers: "как его", "ну типа", "э вот", "ну вот"
    re.compile(r"^(?:как\s+(?:его|её|это)|ну\s+(?:типа|вот)|э\s+вот|вот\s+что),?\s+"),
    # UK extra: "як його", "ну от", "е от"
    re.compile(r"^(?:як\s+(?:його|її|це)|ну\s+от|е\s+от),?\s+"),
)

_TRAILING_PUNCT = re.compile(r"[\s.!?,]+$")


def _normalize(text: str) -> str:
    """Lowercase, strip, drop trailing punctuation, peel off fillers
    iteratively. Returns the form the pattern table sees."""
    s = text.lower().strip()
    s = _TRAILING_PUNCT.sub("", s)
    # Iteratively peel fillers (e.g. "could you please open spotify"
    # -> "please open spotify" -> "open spotify").
    while True:
        new_s = s
        for pat in _FILLERS:
            new_s = pat.sub("", new_s, count=1)
        if new_s == s:
            break
        s = new_s
    return s


# --- pattern layer ---------------------------------------------------

# Standalone stop commands. Checked before the normal pattern table so
# "hey jarvis, stop" never accidentally falls through to a tool pattern.
# Normalized text (lowercase, no trailing punctuation) is matched here.
# Multi-word variants ("shut up", "be quiet", "never mind") handled via
# \s+ so Whisper's spacing choices don't matter. Does NOT match commands
# with an object ("stop the music", "cancel that download") — those
# intentionally miss and fall through to the LLM.
_STOP_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:stop|shut\s+up|quiet|be\s+quiet|never\s*mind|cancel"
    r"|that'?s\s+enough|enough"
    # RU: замолчи, тихо, хватит, отмена, достаточно, молчи
    r"|замолчи|тихо|хватит|отмена|достаточно|молчи|заткнись"
    # UK: замовчи, тихо, досить, скасуй, мовчи
    r"|замовчи|досить|скасуй|мовчи)$",
    re.IGNORECASE,
)

# Game mode voice patterns — matched after stop, before tool patterns.
# "game mode" / "игровой режим" / "ігровий режим" → show status
# "включи / выключи / enable / disable game mode" → force activate/deactivate
_GAME_MODE_STATUS_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:game\s*mode|игровой\s*режим|ігровий\s*режим)$",
    re.IGNORECASE,
)
_GAME_MODE_ON_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:(?:включи|enable|activate|start)\s+(?:game\s*mode|игровой\s*режим|ігровий\s*режим)"
    r"|game\s*mode\s+(?:on|enable|start|activate)"
    r"|(?:game\s*mode|игровой\s*режим|ігровий\s*режим)\s+(?:on|вкл|включи))$",
    re.IGNORECASE,
)
_GAME_MODE_OFF_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:(?:выключи|отключи|disable|deactivate|stop)\s+(?:game\s*mode|игровой\s*режим|ігровий\s*режим)"
    r"|game\s*mode\s+(?:off|disable|stop|deactivate)"
    r"|(?:game\s*mode|игровой\s*режим|ігровий\s*режим)\s+(?:off|выкл|выключи))$",
    re.IGNORECASE,
)


def _what_time_intent(
    time_provider: Callable[[], datetime.datetime],
) -> SpeakIntent:
    now = time_provider()
    formatted = now.strftime("%I:%M %p").lstrip("0")
    return SpeakIntent(text=f"It's {formatted}, sir.")


def _build_patterns(
    time_provider: Callable[[], datetime.datetime],
) -> list[tuple[re.Pattern, Callable[[re.Match], Intent]]]:
    r"""Assemble the first-match-wins pattern table.

    The table used to be a hand-ordered literal here; the regexes now live
    on the tool classes they dispatch to, as `voice_patterns` class
    attributes (see `jarvis.tools.registry.VoicePattern`), and are
    collected by `jarvis.tools.catalogue.voice_pattern_catalogue()`.

    ORDERING IS BEHAVIOUR. The layer is first-match-wins, so the position
    of a pattern decides which tool wins an utterance two patterns could
    both match. Order therefore comes from each pattern's explicit integer
    `priority` (lower runs first) and from nothing else — not declaration
    order, not catalogue order, and above all not registration order. The
    canonical example: `open_app`'s `^open\s+(.+)$` carries
    PRIORITY_CATCH_ALL so "open my notes", "open the dashboard", "open
    logs", "open my workspace", "open clipboard history" and "open help"
    are each claimed by their own tool first. `tests/llm/
    test_router_pattern_equivalence.py` pins the assembled order against a
    golden captured before this refactor.

    The two time patterns below are the only router-native entries left:
    they answer locally with a SpeakIntent and have no tool to hang off.
    They are merged into the same priority-sorted sequence rather than
    being special-cased at the front, so their precedence is stated the
    same way every other pattern's is.

    The catalogue import is function-local (the same idiom as
    `jarvis.tools.setup_local_tools`) so importing the router does not pull
    in every tool module; this runs once per IntentRouter construction.
    """
    from jarvis.tools.catalogue import voice_pattern_catalogue

    entries: list[tuple[int, int, str, Callable[[re.Match], Intent]]] = []

    # -- router-native patterns (answered locally, no tool involved) ---
    # "what's the time" / "what is the time" / "what time is it".
    entries.append((
        40, 0,
        r"^what(?:'s|\s+is)\s+(?:the\s+)?time(?:\s+is\s+it)?$",
        lambda m: _what_time_intent(time_provider),  # noqa: ARG005
    ))
    entries.append((
        50, 1,
        r"^what\s+time\s+is\s+it$",
        lambda m: _what_time_intent(time_provider),  # noqa: ARG005
    ))

    # -- tool-declared patterns ----------------------------------------
    for seq, (tool_name, vp) in enumerate(voice_pattern_catalogue()):
        entries.append((
            vp.priority,
            seq,
            vp.regex,
            # Defaults bind the loop variables; without them every builder
            # would close over the last iteration's tool.
            lambda m, _name=tool_name, _args=vp.args: ToolIntent(
                _name, _args(m)
            ),
        ))

    # Stable sort on (priority, tiebreak). The tiebreak only ever fires
    # for two patterns given the same number, which the built-in set
    # never does.
    entries.sort(key=lambda e: (e[0], e[1]))
    return [(re.compile(regex), builder) for _p, _s, regex, builder in entries]


# --- router ----------------------------------------------------------


class IntentRouter:
    def __init__(
        self,
        *,
        llm: OllamaClient,
        conversation: Conversation,
        tools: list[dict] | None = None,
        registry: ToolRegistry | None = None,
        time_provider: Callable[[], datetime.datetime] = datetime.datetime.now,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
        # Optional fast path for "open <known site/app>" in any language.
        # Called before the pattern table and the LLM; returns a ToolIntent
        # (open_app / open_url) or None to fall through. None = disabled,
        # which keeps the router's behaviour unchanged (and tests free of
        # this PC's installed-apps index).
        open_target_planner: Callable[[str], Awaitable[ToolIntent | None]] | None = None,
        # Preferred: pass a PersonalizationContext grouping all stores.
        personalization: PersonalizationContext | None = None,
        # DEPRECATED individual params — kept for backward compat with
        # existing callers and tests. When *personalization* is provided
        # these are ignored; otherwise they seed a new context.
        memory_store: MemoryStore | None = None,
        tool_example_store: ToolExampleStore | None = None,
        relation_store: RelationStore | None = None,
        personal_aliases: PersonalAliasStore | None = None,
        user_profile: UserProfile | None = None,
        usage_stats: UsageStats | None = None,
        # Phase 12: User-defined voice protocols
        protocol_engine: ProtocolEngine | None = None,
    ) -> None:
        # `registry` is the live source of the tool schemas — querying it
        # fresh on every route() call means a settings-driven enable/disable
        # takes effect on the very next turn (no rebuild of the router).
        # It is ALSO the executor for the tool-result feedback loop; a
        # router without one cannot observe results and so degrades to
        # one-shot dispatch (yield ToolIntents, caller executes).
        # `tools` is the legacy static-list path retained for the existing
        # test suite and any caller that wants to inject a curated schema
        # set; if both are provided, registry wins.
        self._llm = llm
        self._conv = conversation
        self._registry = registry
        self._static_tools = tools or []
        self._patterns = _build_patterns(time_provider)
        self._max_tool_iterations = max(1, int(max_tool_iterations))
        self._open_target_planner = open_target_planner
        # Ollama does not attach ids to the tool calls it emits, so the
        # router mints them. Monotonic over the router's lifetime rather
        # than per-turn: conversation history outlives a turn, and two
        # round trips sharing "call_1" would let the trimming/orphan
        # filter pair a tool result with the wrong assistant message.
        self._tool_call_seq = 0

        # Phase 12: protocol engine for user-defined voice macros.
        self._protocol_engine = protocol_engine

        # --- personalization context ---
        # Build from individual params when the caller hasn't passed a
        # PersonalizationContext (backward compat for tests & callers).
        if personalization is not None:
            self._pctx = personalization
        else:
            from jarvis.core.personalization import PersonalizationContext
            self._pctx = PersonalizationContext(
                memory_store=memory_store,
                relation_store=relation_store,
                tool_examples=tool_example_store,
                personal_aliases=personal_aliases,
                user_profile=user_profile,
                usage_stats=usage_stats,
            )

        # Game mode: usually set via personalization, but app.py may also
        # assign it after construction (avoids circular deps).
        if self._pctx.game_mode is None:
            self._pctx.game_mode = None  # explicit sentinel

        # Security Layers 1+2: ToolGuard gates LLM-initiated network
        # tool calls through the DataLeakScanner.  Created lazily from
        # the personalization context so app.py needs no changes.
        #
        # If construction fails, we do NOT silently disable the guard:
        # _security_degraded is set, an ERROR is logged, and the LLM
        # execution path (_run_tools) fails CLOSED for network/exec tools.
        self._security_degraded = False
        try:
            from jarvis.security.tool_guard import ToolGuard
            from jarvis.security.leak_scanner import DataLeakScanner

            self._tool_guard: ToolGuard | None = ToolGuard(
                leak_scanner=DataLeakScanner(user_profile=self._pctx.user_profile),
            )
        except Exception:
            self._tool_guard = None
            self._mark_security_degraded(
                component="ToolGuard",
                detail="ToolGuard/DataLeakScanner construction failed; "
                "LLM-initiated network/exec tools will be blocked.",
            )

        # Security Layer 3: OutputSanitizer — a heuristic SECOND line of
        # defence behind ToolGuard. In this "soft" wiring it logs + audits
        # suspicious tool calls and exfiltration chains but does NOT block
        # them (ToolGuard + risk-level confirmations are the hard gate);
        # the ONE thing it blocks is a round whose LLM text carries a
        # prompt-injection marker, because executing tool calls parsed from
        # a compromised response is the exact chain SECURITY.md scopes in.
        try:
            from jarvis.security.output_sanitizer import OutputSanitizer

            self._sanitizer: OutputSanitizer | None = OutputSanitizer()
        except Exception:
            self._sanitizer = None
            # Soft layer: a failure does NOT block, but it must not vanish
            # silently either — log ERROR once and flag degraded mode.
            self._mark_security_degraded(
                component="OutputSanitizer",
                detail="OutputSanitizer construction failed; the heuristic "
                "sanitizer layer is inactive (soft layer, non-blocking).",
            )
        # Rolling window of recent tool calls (chronological) for
        # check_tool_chain(). Capped so a long session cannot grow it
        # without bound; the suspicious chains we look for are short.
        self._recent_tool_calls: list[dict] = []
        self._recent_calls_cap = 20

    def _mark_security_degraded(self, *, component: str, detail: str) -> None:
        """Record that a security layer failed to initialise.

        Logs an ERROR (visible in the log panel — the existing notification
        surface), records a one-shot audit entry when an audit log is
        available, and sets ``self._security_degraded`` so the LLM execution
        path can fail closed. Idempotent: the ERROR banner is emitted once
        per session even if both layers fail.
        """
        self._security_degraded = True
        log.error(
            "[security] DEGRADED MODE — %s unavailable: %s", component, detail,
            exc_info=True,
        )
        # One-shot user-facing banner (log panel is the existing surface;
        # we do not invent a new notification mechanism here).
        if not getattr(self, "_degraded_logged", False):
            self._degraded_logged = True
            log.error(
                "[security] Jarvis security is running in LIMITED mode; "
                "LLM-initiated network/exec tools are blocked until restart.",
            )
        audit = getattr(self._pctx, "audit_log", None)
        if audit is not None:
            try:
                audit.log_blocked(
                    tool_name="<init>",
                    reason="security_layer_init_failed",
                    details=f"{component}: {detail}",
                )
            except Exception:
                log.debug(
                    "[security] audit log write failed for degraded mode",
                    exc_info=True,
                )

    def _current_tools(self) -> list[dict]:
        if self._registry is not None:
            return self._registry.as_openai_functions()
        return self._static_tools

    def _filtered_tools(self, utterance: str) -> list[dict]:
        """Return tool schemas filtered to those relevant for *utterance*.

        Wraps ``_current_tools()`` through the keyword-based filter so the
        LLM sees only the tools it is likely to need, freeing context
        tokens for conversation history.  Falls back to the full set when
        no keywords match (safe default)."""
        all_tools = self._current_tools()
        if not all_tools:
            return all_tools
        from jarvis.tools.tool_filter import filter_tools_for_utterance
        return filter_tools_for_utterance(utterance, all_tools)

    def _feedback_enabled(self) -> bool:
        """True when the router can run the tool-result loop itself."""
        return self._registry is not None and self._max_tool_iterations > 1

    def _next_call_id(self) -> str:
        self._tool_call_seq += 1
        return f"call_{self._tool_call_seq}"

    async def route(self, transcription: str) -> AsyncGenerator[Intent, None]:
        """Route a transcription into a stream of Intents.

        Typed as AsyncGenerator rather than AsyncIterator (it satisfies
        IntentProducer either way) so callers can aclose() it
        explicitly. A consumer cancelled mid-turn leaves this generator
        suspended inside stream_chat or a tool; closing it promptly is
        cheaper and more predictable than waiting for the GC.

        Pattern matches yield exactly one Intent and return — no LLM
        call, no added latency, unchanged by the feedback loop.

        LLM responses run the tool-result loop: up to
        max_tool_iterations model invocations, executing tool calls and
        feeding their results back between rounds. The Intents yielded
        are the SpeakIntents of whichever round finally answered
        without calling a tool. When the loop is disabled (no registry,
        or max_tool_iterations == 1) tool calls are yielded as
        ToolIntents for the caller to execute instead."""
        # --- Dictation mode: bypass all routing, type directly --------
        # Function-local import: avoids importing tool modules at
        # router import time (same idiom as _build_patterns).
        from jarvis.tools.local.dictation import (
            is_dictation_active,
            DICTATION_STOP_PATTERN,
            process_dictation_text,
            type_dictation_text,
        )

        if is_dictation_active():
            normalized = _normalize(transcription)
            if not DICTATION_STOP_PATTERN.match(normalized):
                processed = process_dictation_text(transcription)
                await type_dictation_text(processed)
                return  # silent — no SpeakIntent
            # Stop command: fall through to normal pattern matching
            # so DictationTool.execute(stop) clears the flag.

        # Known open targets are decided in code: the local model picked
        # open_app vs open_url inconsistently for the same phrase. Unknown
        # names return None here and continue down the usual path.
        target_intent = await self._try_open_target(transcription)
        if target_intent is not None:
            yield target_intent
            return

        # Phase 12: user-defined protocols — checked BEFORE tool patterns
        # so a protocol named e.g. "Party" is matched before any regex
        # pattern could accidentally claim the word.
        if self._protocol_engine is not None:
            normalized_for_proto = _normalize(transcription)
            matched_protocol = self._protocol_engine.match_protocol(
                normalized_for_proto,
            )
            if matched_protocol is not None:
                yield ToolIntent(
                    "run_protocol",
                    {"name": matched_protocol.name},
                )
                return

        pattern_intent = self._try_pattern(transcription)
        if pattern_intent is not None:
            # Game mode on/off: fire the async action before yielding
            if self._pctx.game_mode is not None:
                normalized = _normalize(transcription)
                if _GAME_MODE_ON_PATTERN.match(normalized):
                    try:
                        await self._pctx.game_mode.force_activate()
                    except Exception:
                        log.warning("game mode force_activate failed", exc_info=True)
                elif _GAME_MODE_OFF_PATTERN.match(normalized):
                    try:
                        await self._pctx.game_mode.force_deactivate()
                    except Exception:
                        log.warning("game mode force_deactivate failed", exc_info=True)
            yield pattern_intent
            return

        # T4.4/T4.5: inject auto-recall memories + tool examples
        await self._inject_auto_context(transcription)

        messages = self._conv.add_user_turn(transcription)

        # Game mode: Ollama is evicted from VRAM.  Use the on-demand
        # slow path (temp load → answer → unload) so LLM questions
        # still work.  This is an early return BEFORE the streaming
        # loop — the on_demand_llm_query response is not streamed,
        # just returned whole.
        if (
            self._pctx.game_mode is not None
            and self._pctx.game_mode.is_active
        ):
            answer = await self._pctx.game_mode.on_demand_llm_query(
                transcription,
            )
            if answer:
                yield SpeakIntent(text=answer)
                self._conv.add_assistant_turn(answer)
            else:
                fallback = say("generic_fail")
                yield SpeakIntent(text=fallback)
            self._conv.clear_injected_context()
            return

        # Dynamic tool filtering
        # utterance, freeing context for conversation history.  Computed
        # once before the loop so every iteration (including tool-result
        # feedback rounds) sees the same subset — the user's intent
        # hasn't changed between iterations.
        tools_for_turn = self._filtered_tools(transcription)

        for iteration in range(1, self._max_tool_iterations + 1):
            text_parts, raw_tool_calls = await self._stream_round(
                messages, tools=tools_for_turn,
            )

            # Security Layer 3: inspect the raw LLM text BEFORE executing
            # any tool calls parsed from it. A prompt-injection marker in
            # the model's own output means the response may be compromised;
            # executing tool calls from it is the exact "injection chain
            # reaching an unrequested tool call" SECURITY.md scopes in, so
            # this is the ONE sanitizer check that blocks (soft mode
            # otherwise only logs/audits). The accumulated text is still
            # spoken so the user is not left in silence.
            if self._sanitizer is not None and raw_tool_calls:
                _llm_res = self._sanitizer.sanitize_llm_response(
                    "".join(text_parts)
                )
                if not _llm_res.safe:
                    self._audit_sanitizer(_llm_res, source="llm_response")
                    log.warning(
                        "[router] suppressing %d tool call(s): injection "
                        "marker in LLM response",
                        len(raw_tool_calls),
                    )
                    for part in text_parts:
                        if part:
                            yield SpeakIntent(text=part)
                    self._conv.clear_injected_context()
                    return

            if not raw_tool_calls:
                # The model answered in prose. This is the only path
                # that speaks model text, and the only one that records
                # a plain assistant turn.
                for part in text_parts:
                    if part:
                        yield SpeakIntent(text=part)
                # T4.4: clear injected context after response is done
                self._conv.clear_injected_context()
                # T4.5: no tool call — nothing to record
                full_text = "".join(text_parts)
                if full_text:
                    self._conv.add_assistant_turn(full_text)
                return

            intents = self._tool_intents_from(raw_tool_calls, transcription)
            if not intents:
                # The model tried to call tools but every call was
                # malformed. Narration is still suppressed (it was
                # written to accompany an action that never happened)
                # and no assistant turn is recorded.
                return

            if not self._feedback_enabled():
                # Legacy one-shot dispatch: the caller executes and
                # speaks. Nothing is stored — the tool result never
                # passes through this method, which is what kept it out
                # of history before the loop existed.
                for intent in intents:
                    yield intent
                return

            # Execute first, record second. Every await lives in
            # _run_tools, so a cancel lands before the round trip is
            # written rather than halfway through it.
            exchanges, spoken, ends_turn = await self._run_tools(intents)
            messages = self._conv.add_tool_round_trip(
                content="".join(text_parts), exchanges=exchanges
            )
            # T4.5: record successful tool calls for self-learning
            self._record_tool_examples(transcription, intents, exchanges)

            # T4.7: log user actions to long-term memory
            self._log_actions(intents, exchanges)

            # T4.8: check event-triggered reminders after app/game launch
            reminder_text = await self._check_launch_reminders(intents, exchanges)

            if ends_turn:
                # Every tool in this round is a one-shot action (open a
                # site / an app) and it succeeded. Asking the model again
                # only lets a small local model re-fire the same action
                # (three browser tabs for one "open YouTube") and costs a
                # full inference. Speak the result and end the turn, like
                # the bound path does. Failures still loop, so the model
                # can recover (e.g. open_app failed -> try open_url).
                log.info(
                    "[router] %s succeeded; ending turn without another model round",
                    ", ".join(i.tool_name for i in intents),
                )
                if spoken:
                    yield SpeakIntent(text=spoken)
                if reminder_text:
                    yield SpeakIntent(text=reminder_text)
                self._conv.clear_injected_context()
                return

            if iteration >= self._max_tool_iterations:
                # Bound reached. Degrade by speaking the results we
                # already have rather than burning another inference or
                # going silent. The results are in history as role:"tool"
                # messages, so they are NOT also stored as assistant
                # text — that duplicate is exactly the shape that used
                # to make the model re-fire tools on follow-ups.
                log.warning(
                    "[router] tool feedback loop hit the %d-iteration "
                    "bound for %r; speaking raw tool output",
                    self._max_tool_iterations,
                    transcription,
                )
                if spoken:
                    yield SpeakIntent(text=spoken)
                if reminder_text:
                    yield SpeakIntent(text=reminder_text)
                self._conv.clear_injected_context()
                return

    # -- event-triggered reminders ----------------------------------------

    async def _check_launch_reminders(
        self,
        intents: list[ToolIntent],
        exchanges: list[ToolExchange],
    ) -> str:
        """Check for event-triggered reminders after app/game launches.

        Inspects the tool intents for launch_steam_game / open_app.
        If found and successful, searches memory for reminders tagged
        with that app/game name.

        Returns formatted spoken reminder text, or empty string.
        Non-fatal: any exception is logged and silently swallowed.
        """
        if self._pctx.memory_store is None:
            return ""

        # Collect app/game names from successful launch tools
        targets: list[str] = []
        for intent, exchange in zip(intents, exchanges):
            if exchange.result.startswith("Error:"):
                continue
            if intent.tool_name == "launch_steam_game":
                name = intent.args.get("game_name") or intent.args.get("name", "")
                if name:
                    targets.append(name)
            elif intent.tool_name == "open_app":
                name = intent.args.get("name") or intent.args.get("app_name", "")
                if name:
                    targets.append(name)

        if not targets:
            return ""

        try:
            from jarvis.memory.reminders import check_reminders, format_reminders_for_speech

            all_reminders: list[str] = []
            for target in targets:
                reminders = await check_reminders(self._pctx.memory_store, target)
                all_reminders.extend(reminders)

            if all_reminders:
                lang = reply_language()
                return format_reminders_for_speech(all_reminders, lang=lang)
        except Exception:
            log.warning("[router] reminder check failed (non-fatal)", exc_info=True)

        return ""

    # -- auto-context injection -------------------------------------------

    async def _inject_auto_context(self, transcription: str) -> None:
        """Build and inject auto-recall memories + tool examples.

        Called once per LLM turn, BEFORE ``add_user_turn``.
        Sets ``_conv.inject_context(...)`` so the memories appear as a
        second system message between the main prompt and history.

        Non-fatal: any exception is logged and silently swallowed.
        """
        parts: list[str] = []  # noqa: RUF012

        # 0. Game mode: inject preloaded game context (RAM, no LLM cost)
        if self._pctx.game_mode is not None and self._pctx.game_mode.is_active:
            ctx = self._pctx.game_mode.preloaded_context
            if ctx:
                parts.append(ctx)
                log.debug(
                    "[router] game mode: injecting %d chars of preloaded context",
                    len(ctx),
                )

        # 1. Auto-recall relevant memories
        if self._pctx.memory_store is not None:
            try:
                from jarvis.memory.auto_recall import get_relevant_memories

                memory_text = await get_relevant_memories(
                    self._pctx.memory_store, transcription,
                    relation_store=self._pctx.relation_store,
                )
                if memory_text:
                    parts.append(memory_text)
            except Exception:
                log.warning("[router] auto-recall failed (non-fatal)", exc_info=True)

        # 2. Tool examples from self-learning
        if self._pctx.tool_examples is not None:
            try:
                examples_text = self._pctx.tool_examples.get_examples_for_prompt()
                if examples_text:
                    parts.append(examples_text)
            except Exception:
                log.warning("[router] tool-examples failed (non-fatal)", exc_info=True)

        # 3. User profile — persistent facts (name, city, etc.)
        if self._pctx.user_profile is not None:
            try:
                profile_text = self._pctx.user_profile.for_prompt()
                if profile_text:
                    parts.append(profile_text)
            except Exception:
                log.warning("[router] user-profile failed (non-fatal)", exc_info=True)

        # 4. Usage stats — frequently used apps
        if self._pctx.usage_stats is not None:
            try:
                stats_text = self._pctx.usage_stats.for_prompt()
                if stats_text:
                    parts.append(stats_text)
            except Exception:
                log.warning("[router] usage-stats failed (non-fatal)", exc_info=True)

        if parts:
            self._conv.inject_context("\n\n".join(parts))
        else:
            self._conv.clear_injected_context()

    def _record_tool_examples(
        self,
        transcription: str,
        intents: list[ToolIntent],
        exchanges: list[ToolExchange],
    ) -> None:
        """Record successful tool calls for self-learning.  Non-fatal."""
        if self._pctx.tool_examples is None:
            return
        for intent, exchange in zip(intents, exchanges):
            # Only record successes (result doesn't start with "Error:")
            if exchange.result.startswith("Error:"):
                continue
            try:
                self._pctx.tool_examples.record_success(
                    transcription, intent.tool_name, intent.args,
                )
            except Exception:
                log.warning("[router] tool-example record failed", exc_info=True)

        # T4.9: Record usage stats for every successful tool call.
        self._record_usage_stats(intents, exchanges)

        # T4.9: Learn personal aliases from successful open_app calls.
        self._learn_personal_aliases(transcription, intents, exchanges)

    def _record_usage_stats(
        self,
        intents: list[ToolIntent],
        exchanges: list[ToolExchange],
    ) -> None:
        """Record tool and app usage stats. Non-fatal."""
        if self._pctx.usage_stats is None:
            return
        for intent, exchange in zip(intents, exchanges):
            if exchange.result.startswith("Error:"):
                continue
            try:
                self._pctx.usage_stats.record_tool_use(intent.tool_name)
                if intent.tool_name == "open_app":
                    app_name = intent.args.get("name", "")
                    if app_name:
                        self._pctx.usage_stats.record_app_use(app_name)
            except Exception:
                log.warning("[router] usage-stats record failed", exc_info=True)

    def _learn_personal_aliases(
        self,
        transcription: str,
        intents: list[ToolIntent],
        exchanges: list[ToolExchange],
    ) -> None:
        """Learn personal aliases from successful open_app calls.

        When the utterance contains a generic word ("браузер", "редактор",
        "player") and the tool argument is a specific app ("chrome",
        "vscode", "vlc"), learn the mapping. Non-fatal.
        """
        if self._pctx.personal_aliases is None:
            return
        # Generic words that likely map to a specific app
        _GENERIC_WORDS = {
            "браузер", "browser", "редактор", "editor", "плеер", "player",
            "плеєр", "мессенджер", "messenger", "месенджер", "почта", "mail",
            "пошта", "музыка", "music", "музика", "архиватор", "archiver",
            "терминал", "terminal", "термінал", "ide", "іде",
        }
        for intent, exchange in zip(intents, exchanges):
            if intent.tool_name != "open_app":
                continue
            if exchange.result.startswith("Error:"):
                continue
            app_arg = intent.args.get("name", "").strip().lower()
            if not app_arg:
                continue
            # Check if the transcription contains a generic word
            transcription_lower = transcription.lower()
            for generic in _GENERIC_WORDS:
                if generic in transcription_lower and generic != app_arg:
                    try:
                        self._pctx.personal_aliases.learn(generic, app_arg)
                        log.info(
                            "[router] learned personal alias: %r → %r",
                            generic, app_arg,
                        )
                    except Exception:
                        log.warning(
                            "[router] personal-alias learn failed",
                            exc_info=True,
                        )
                    break  # one generic word per utterance is enough

    def _log_actions(
        self,
        intents: list[ToolIntent],
        exchanges: list[ToolExchange],
    ) -> None:
        """Log successful tool calls as user actions to long-term memory.

        Fire-and-forget via asyncio.create_task — never blocks pipeline.
        Non-fatal: any exception is logged and silently swallowed.
        """
        if self._pctx.memory_store is None:
            return
        from jarvis.memory.action_logger import log_action

        for intent, exchange in zip(intents, exchanges):
            success = not exchange.result.startswith("Error:")
            try:
                asyncio.create_task(
                    log_action(self._pctx.memory_store, intent.tool_name, intent.args, success=success)
                )
            except Exception:
                log.warning("[router] action-log failed", exc_info=True)

    # -- internal --

    async def _stream_round(
        self, messages: list[dict],
        tools: list[dict] | None = None,
    ) -> tuple[list[str], list[dict]]:
        """One LLM invocation. Returns (content chunks, raw tool calls).

        Text is accumulated rather than yielded because tool calls
        arrive on the final chunk — see the module's "Double-speaking"
        note. Chunk granularity is preserved in the returned list so
        the caller can hand PiperTTS the same token-sized pieces it
        would have got streaming.

        CancelledError propagates out untouched: no try/except here,
        and the caller has written nothing to history yet.

        Per-chunk timeout: if Ollama stops sending data for
        _STREAM_CHUNK_TIMEOUT_S seconds the stream is abandoned and
        an error message is returned so the user always hears
        something rather than hanging forever."""
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        stream = self._llm.stream_chat(
            messages, tools=tools if tools is not None else self._current_tools(),
        )
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=_STREAM_CHUNK_TIMEOUT_S,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    log.error("[router] LLM stream stalled for %.0fs — aborting round", _STREAM_CHUNK_TIMEOUT_S)
                    text_parts.append(say("generic_fail"))
                    break
                if chunk.content:
                    text_parts.append(chunk.content)
                if chunk.tool_calls:
                    # Per current Ollama behavior, tool_calls arrive complete
                    # on the final chunk. Defensive accumulation here is the
                    # hook point for future per-chunk streaming -- if Ollama
                    # adopts incremental tool-call emission, partial-call
                    # assembly logic plugs in around this list.
                    tool_calls.extend(chunk.tool_calls)
                if chunk.done:
                    break
        finally:
            await stream.aclose()
        return text_parts, tool_calls

    def _tool_intents_from(
        self, raw_tool_calls: list[dict], transcription: str
    ) -> list[ToolIntent]:
        """Parse, log and de-duplicate one round's tool calls."""
        # Visibility for live tool-decision debugging: every LLM-chosen
        # tool is traced alongside the transcription that prompted it.
        # The pattern path doesn't show up here — those are router-level
        # decisions, not LLM ones.
        #
        # This is DEBUG, not INFO, for one reason: `transcription` is the
        # user's speech. It is per-interaction trace that only matters
        # when someone is asking "why did it call that tool?", so it is
        # opt-in via Settings -> Log level, and the %r is not formatted
        # at all until then.
        for tc in raw_tool_calls:
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or "<missing>"
            log.debug(
                "[router] llm-chose-tool=%s for=%r", tool_name, transcription
            )
        if len(raw_tool_calls) > 1:
            log.info(
                "[router] ToolIntent count=%d in one response",
                len(raw_tool_calls),
            )
        # De-duplication is per response, not across iterations: a model
        # that legitimately re-reads a file after writing it must be
        # allowed to. Cross-iteration repetition is what the iteration
        # bound is for.
        seen_tool_keys: set[str] = set()
        intents: list[ToolIntent] = []
        for tc in raw_tool_calls:
            intent = self._tool_intent_from(tc)
            if intent is None:
                continue
            key = json.dumps(
                {"name": intent.tool_name, "args": intent.args}, sort_keys=True
            )
            if key in seen_tool_keys:
                log.info(
                    "[router] skipped duplicate tool call: %s(%r)",
                    intent.tool_name,
                    intent.args,
                )
                continue
            seen_tool_keys.add(key)
            intents.append(intent)
        return intents

    async def _run_tools(
        self, intents: list[ToolIntent]
    ) -> tuple[list[ToolExchange], str, bool]:
        """Execute each ToolIntent; return the exchanges to record and
        the spoken form of the results (used only if the loop then hits
        its iteration bound or the turn ends early).

        The third value is True when EVERY executed tool declares
        `ends_turn_on_success = True` and succeeded: the caller then ends
        the turn instead of re-invoking the model. Tools without the
        attribute never end the turn, so default behaviour is unchanged.

        Writes nothing to conversation history — the caller appends the
        whole round trip atomically once every tool has finished, so a
        cancel mid-execution leaves history exactly as it was.
        registry.execute() never raises for tool-level failures (it
        returns ToolResult(success=False)), but does re-raise
        CancelledError, which is the behaviour this relies on."""
        registry = self._registry
        assert registry is not None  # guarded by _feedback_enabled()
        exchanges: list[ToolExchange] = []
        spoken: list[str] = []
        ends_turn = bool(intents)
        for intent in intents:
            # Security Layer 3 (soft): record the call in the rolling window
            # and run the heuristic sanitizer. These log + audit but do NOT
            # block — ToolGuard below is the hard gate.
            if self._sanitizer is not None:
                self._recent_tool_calls.append({"tool_name": intent.tool_name})
                if len(self._recent_tool_calls) > self._recent_calls_cap:
                    self._recent_tool_calls = self._recent_tool_calls[
                        -self._recent_calls_cap:
                    ]
                _call_res = self._sanitizer.sanitize_tool_call(
                    intent.tool_name, intent.args,
                )
                if not _call_res.safe:
                    self._audit_sanitizer(
                        _call_res, source=f"tool_call:{intent.tool_name}",
                    )
                _chain_res = self._sanitizer.check_tool_chain(
                    self._recent_tool_calls,
                )
                if not _chain_res.safe:
                    self._audit_sanitizer(_chain_res, source="tool_chain")

            # Security Layers 1+2: gate LLM-initiated calls through
            # ToolGuard before execution.  The context is always
            # LLM_TOOL_CALL here (the LLM chose the tool, not a
            # voice-pattern match), so the leak scanner applies its
            # strictest rules.
            _sec_blocked = False
            if self._tool_guard is not None:
                from jarvis.security.origin_tracker import (
                    RequestContext as _RC,
                    RequestOrigin as _RO,
                    get_request_context as _get_ctx,
                )
                _user_ctx = _get_ctx()
                _llm_ctx = _RC(
                    origin=_RO.LLM_TOOL_CALL,
                    token="",
                    timestamp=_user_ctx.timestamp,
                    original_text=_user_ctx.original_text,
                )
                _guard = self._tool_guard.check(
                    intent.tool_name, intent.args, _llm_ctx,
                )
                if not _guard.allowed:
                    from jarvis.tools.registry import ToolResult
                    result = ToolResult(
                        success=False,
                        error=f"Security: {_guard.reason}",
                    )
                    _sec_blocked = True
            else:
                # ToolGuard failed to initialise (degraded mode). Fail
                # CLOSED: block LLM-initiated network/exec tools entirely,
                # since the leak scan that would normally gate them is gone.
                # A tool that is both RiskLevel.READ and neither network nor
                # exec is safe to run unguarded (read-only, no egress), so
                # read-only queries still work.
                from jarvis.security.tool_guard import EXEC_TOOLS, NETWORK_TOOLS
                from jarvis.tools.risk import RiskLevel, risk_level_for_tool
                from jarvis.tools.registry import ToolResult

                _tool = registry.get(intent.tool_name)
                _is_sensitive = (
                    intent.tool_name in NETWORK_TOOLS
                    or intent.tool_name in EXEC_TOOLS
                )
                _is_read_only = (
                    _tool is not None
                    and risk_level_for_tool(_tool) == RiskLevel.READ
                )
                if _is_sensitive or not _is_read_only:
                    log.error(
                        "[security] blocking LLM-initiated %r: guard "
                        "unavailable (degraded mode)", intent.tool_name,
                    )
                    result = ToolResult(
                        success=False,
                        error="Security: guard unavailable",
                    )
                    _sec_blocked = True
            if not _sec_blocked:
                result = await registry.execute(intent.tool_name, intent.args)
            tool = registry.get(intent.tool_name)
            ends_turn = ends_turn and result.success and bool(
                getattr(tool, "ends_turn_on_success", False)
            )
            exchanges.append(
                ToolExchange(
                    call_id=self._next_call_id(),
                    name=intent.tool_name,
                    arguments=intent.args,
                    result=_result_for_model(result),
                )
            )
            spoken.append(_result_for_speech(result))
        return exchanges, "".join(spoken), ends_turn

    def _audit_sanitizer(self, result, source: str) -> None:
        """Record a Layer-3 sanitizer trigger (log + optional audit log).

        Soft mode: this is observability, not enforcement. Never raises and
        never blocks the caller. Logs a warning always; also appends to the
        tamper-evident audit log when one is wired into the personalization
        context. No secrets/paths are logged — only the rule names + reason.
        """
        log.warning(
            "[output-sanitizer] %s: %s (patterns=%s, severity=%s)",
            source,
            result.reason,
            list(result.matched_patterns),
            result.severity,
        )
        audit = getattr(self._pctx, "audit_log", None)
        if audit is None:
            return
        try:
            for pattern_name in (result.matched_patterns or ("unknown",)):
                audit.log_sanitizer_trigger(
                    pattern_name=pattern_name,
                    content_preview=result.reason,
                    source=source,
                )
        except Exception:
            log.debug("[output-sanitizer] audit log write failed", exc_info=True)

    async def _try_open_target(self, transcription: str) -> ToolIntent | None:
        planner = self._open_target_planner
        if planner is None:
            return None
        try:
            intent = await planner(transcription)
        except Exception:
            # Never lose a turn to the fast path: fall back to patterns/LLM.
            log.warning("[router] open-target planner failed; using the LLM", exc_info=True)
            return None
        if intent is None:
            return None
        if self._registry is not None and self._registry.get(intent.tool_name) is None:
            log.info(
                "[router] open target needs tool %r, which is not registered; "
                "falling through",
                intent.tool_name,
            )
            return None
        return intent

    def _try_pattern(self, transcription: str) -> Intent | None:
        normalized = _normalize(transcription)
        if not normalized:
            return None
        if _STOP_PATTERN.match(normalized):
            return StopIntent()
        # Game mode voice commands (before tool patterns)
        if self._pctx.game_mode is not None:
            if _GAME_MODE_STATUS_PATTERN.match(normalized):
                info = self._pctx.game_mode.get_game_session_info()
                if info:
                    return SpeakIntent(text=info)
                return SpeakIntent(text="Game mode is not active, sir.")
            if _GAME_MODE_ON_PATTERN.match(normalized):
                # force_activate is async; wrap in a ToolIntent-like
                # SpeakIntent and handle activation in route()
                return SpeakIntent(
                    text="Activating game mode, sir. Models will be evicted from VRAM."
                )
            if _GAME_MODE_OFF_PATTERN.match(normalized):
                return SpeakIntent(
                    text="Deactivating game mode, sir. Restoring full capabilities."
                )
        for pattern, builder in self._patterns:
            m = pattern.match(normalized)
            if m is None:
                continue
            intent = builder(m)
            # If the pattern produced a ToolIntent whose tool isn't
            # registered (config disabled it, or the pattern table is
            # ahead of the registry), fall through to the LLM rather
            # than dispatch an "unknown tool" error. The LLM may have
            # a different path to satisfy the request, or it'll explain
            # the limitation in-character.
            if (
                self._registry is not None
                and isinstance(intent, ToolIntent)
                and self._registry.get(intent.tool_name) is None
            ):
                log.info(
                    "pattern matched tool %r but it is not registered; "
                    "falling through to LLM",
                    intent.tool_name,
                )
                return None
            return intent
        return None

    def _tool_intent_from(self, tool_call: dict) -> ToolIntent | None:
        """Convert an Ollama tool_call dict into a ToolIntent. Tolerant
        of the schema variations across Ollama versions: function.name +
        function.arguments where arguments may be a dict or a JSON
        string."""
        try:
            fn = tool_call.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    log.warning(
                        "tool_call arguments not valid JSON: %r", args[:200]
                    )
                    return None
            if not name:
                log.warning("tool_call missing name: %r", tool_call)
                return None
            return ToolIntent(tool_name=name, args=args or {})
        except Exception:
            log.exception("malformed tool_call: %r", tool_call)
            return None


# --- intent → speech adapter ----------------------------------------


# Spoken fallback when a tool succeeds but returns no human-facing
# output. Kept short and in-persona. English form; the spoken one comes
# from _generic_ok() in the request's language.
_GENERIC_OK = "Done, sir."
# Spoken fallback when a tool returns no error string either. Most
# tools do populate .error; this guards the rare "False and silent" case
# rather than spitting an empty string at the user.
_GENERIC_FAIL = "I couldn't do that, sir."


def _generic_ok() -> str:
    return say("done")


def _generic_fail() -> str:
    return say("generic_fail")


def _spoken_error(result: ToolResult) -> str:
    """What to SAY when a tool failed.

    Tool errors are English and often technical (they are written for the
    model and the log). English requests keep hearing them as before; for
    ru/uk requests the voice says a short phrase in that language instead
    of reading English it would mispronounce. The full error is still in
    the log ([tool] ... failed) and in the model's tool message."""
    if reply_language() == "en":
        return result.error or _generic_fail()
    return _generic_fail()


# Characters that PiperTTS's sentence-boundary regex treats as terminators.
# Without one of these the speak_stream buffer waits up to max_wait_seconds
# before flushing, so two back-to-back tool outputs run together.
_SENTENCE_TERMINATORS = (".", "!", "?")

# Markdown that the LLM sometimes emits in SpeakIntent content (link
# syntax, emphasis, code spans). TTS reads it literally — "[Google]
# (https://google.com)" became "open bracket Google close bracket open
# paren h-t-t-p-s..." in live testing. Strip the markdown punctuation
# while keeping the readable inner text.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")


def _scrub_for_speech(text: str) -> str:
    """Strip common Markdown punctuation from text destined for TTS.
    Applied in execute_intent so every Intent-yielded chunk benefits."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)
    return text


def _ensure_sentence_terminator(text: str) -> str:
    """Append '. ' to text that doesn't already end on a sentence
    terminator, so consecutive ToolIntent outputs become separate
    sentences in PiperTTS's segmenter. Trailing whitespace is normalised
    first because outputs sometimes carry a trailing newline from
    psutil / pathlib that defeats the suffix check."""
    stripped = text.rstrip()
    if not stripped:
        return text
    if stripped[-1] in _SENTENCE_TERMINATORS:
        return stripped + " "
    return stripped + ". "


def _result_for_model(result: ToolResult) -> str:
    """Stringify a ToolResult for the role:"tool" message the model reads.

    Deliberately NOT the same rendering as the spoken path. Speech
    collapses a dict result to "Done, sir." because a listener cannot
    consume structured data; the model can, and that structure is the
    whole point of "list my Downloads folder and tell me which one is
    the invoice" — so dicts are serialised as JSON rather than dropped.

    Failures are surfaced verbatim too. A model told "Error: unknown
    tool 'weathr'" can pick a different tool on the next iteration; one
    told nothing just calls the same broken tool again."""
    if not result.success:
        text = f"Error: {result.error or 'the tool failed'}"
    elif result.output is None:
        text = "Success (no output)."
    elif isinstance(result.output, str):
        text = result.output or "Success (no output)."
    else:
        try:
            text = json.dumps(result.output, default=str)
        except (TypeError, ValueError):
            text = str(result.output)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + _TRUNCATION_NOTE
    return text


def _result_for_speech(result: ToolResult) -> str:
    """Spoken rendering of a ToolResult, matching execute_intent's
    ToolIntent branch. Used only on the bound-exhausted path, where
    there is no further model round to summarise the result."""
    if not result.success:
        return _ensure_sentence_terminator(_spoken_error(result))
    if isinstance(result.output, str) and result.output:
        return _ensure_sentence_terminator(result.output)
    # Terminated (unlike execute_intent's bare _GENERIC_OK) because the
    # bound-exhausted path may concatenate several results into one
    # SpeakIntent and PiperTTS segments on sentence boundaries.
    return _ensure_sentence_terminator(_generic_ok())


async def execute_intent(
    intent: Intent,
    registry: ToolRegistry | None,
    on_tool_success: Callable[[str, dict], None] | None = None,
) -> AsyncIterator[str]:
    """Convert one Intent into spoken text chunks.

    SpeakIntent yields its text verbatim. ToolIntent dispatches through
    the registry and speaks the result (output on success, error on
    failure, persona fallback if both are absent). If
    intent.spoken_response is set, that line is spoken *before* the
    tool runs — useful for actions where the LLM wants to narrate the
    action before any output appears ("Locking the screen now, sir.").

    A None registry on a ToolIntent yields an error message — the
    bridge between Intent and execution should always be wired in the
    composition root, so a None here is a configuration bug, not a
    user-visible failure mode. CompoundIntent recurses across its
    children in order."""
    if isinstance(intent, StopIntent):
        return  # silence — no TTS, pipeline transitions to IDLE via zero-yield path
    if isinstance(intent, SpeakIntent):
        yield _scrub_for_speech(intent.text)
        return
    if isinstance(intent, ToolIntent):
        if intent.spoken_response:
            yield _ensure_sentence_terminator(
                _scrub_for_speech(intent.spoken_response)
            )
        if registry is None:
            yield _generic_fail()
            log.error("execute_intent: no registry wired for ToolIntent")
            return
        result = await registry.execute(intent.tool_name, intent.args)
        if result.success:
            # Learning hook for callers that execute tools outside the
            # router's feedback loop (pattern/snap commands and one-shot
            # mode). Guarded and best-effort: learning must never break
            # speech. No-op when the caller passes nothing.
            if on_tool_success is not None:
                try:
                    on_tool_success(intent.tool_name, intent.args)
                except Exception:
                    log.warning(
                        "execute_intent: on_tool_success hook failed",
                        exc_info=True,
                    )
            if isinstance(result.output, str) and result.output:
                yield _ensure_sentence_terminator(
                    _scrub_for_speech(result.output)
                )
            elif result.output is None:
                yield _generic_ok()
            else:
                # dict output: the LLM/UI consumes structured data; spoken
                # layer just confirms the action happened.
                yield _generic_ok()
        else:
            yield _ensure_sentence_terminator(
                _scrub_for_speech(_spoken_error(result))
            )
        return
    if isinstance(intent, CompoundIntent):
        for sub in intent.intents:
            async for chunk in execute_intent(sub, registry, on_tool_success):
                yield chunk
        return
