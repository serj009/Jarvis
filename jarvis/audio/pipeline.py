"""Audio pipeline orchestrator. Owns the audio source, routes frames per
ConversationalState, drives SM transitions, and runs the response chain.

See `audio/protocols.py` for the stage interface contracts and constants.
The design rationale (queue topology, barge-in mechanics, listening-timeout
ownership, frame format trust, TTS-feedback grace window) is documented in
the Phase 2 Task 1 design note in the project history.

Interrupt mechanism
-------------------
Wake word is fed frames in ALL states (IDLE, LISTENING, THINKING, SPEAKING).
When the wake word fires during SPEAKING or THINKING, the pipeline treats it
as an explicit interrupt: it cancels the in-flight response chain (TTS abort
+ task cancel), clears conversation history via on_wake, and transitions
directly to LISTENING. This avoids speaker-to-mic feedback false positives
because openWakeWord is trained specifically on "hey jarvis" phonemes — normal
TTS speech ("Paris", "volume", "of course") will not trigger it.

Known limitation — wake-word interrupt on speaker-based setups
--------------------------------------------------------------
Wake-word interrupt works reliably only when TTS output does not reach the
microphone. On a typical desktop setup (speakers + desk mic, no AEC), Jarvis's
own speech bleeds into the mic and masks the user's "hey jarvis": the combined
signal drops the openWakeWord score below the detection threshold even though
the code path is architecturally correct. Live testing confirmed this: the
handler (_handle_wake_detection) is never reached, not because frames are
skipped (they are fed unconditionally) but because the model simply does not
fire.

Workaround: use headphones. With headphones the mic captures only the user's
voice, and interrupt works as designed.

AEC integration (T3.3)
----------------------
An optional BargeInManager (see audio/aec.py) can be passed to the pipeline.
When configured in AEC mode (with pywebrtc-audio or echoff installed), it
subtracts the speaker reference from the mic signal so VAD and the wake-word
classifier receive a clean input even while TTS is playing. Without the AEC
library, the manager falls back to half-duplex mode automatically.

VAD-based barge-in is a separate mechanism and remains disabled by default
(`BARGE_IN_ENABLED = False`): without AEC, desktop speakers feeding back into
the desk mic fire VAD's SPEECH_STARTED mid-response and cause self-interrupts.
Re-enable when AEC is implemented (Phase 6) or on headphone-only deployments —
pass `barge_in_enabled=True` to the constructor. The barge-in code path is
intact and complementary to wake-word interrupt; they are not mutually exclusive.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from jarvis.audio.aec import BargeInManager, BargeInMode
from jarvis.audio.protocols import (
    FRAME_BYTES,
    FRAME_DURATION_MS,
    LISTENING_TIMEOUT_DEFAULT,
    MIN_UTTERANCE_MS,
    POST_WAKE_BLACKOUT_MS,
    SAMPLE_RATE,
    TTS_BARGE_IN_GRACE_SECONDS,
    AudioFrame,
    AudioSource,
    ResponseProducer,
    SpeechToText,
    TextToSpeech,
    VADEvent,
    VoiceActivityDetector,
    WakeWordDetector,
    WakeWordResult,
)
from jarvis.audio.sound_effects import SoundEffects
from jarvis.core.events import (
    ConversationalStateChanged,
    EventBus,
    LLMResponseChunk,
    LLMResponseComplete,
    ModeChanged,
    NonFatalError,
    ProactiveSpeech,
    TranscriptionReady,
    WakeWordDetected,
)
from jarvis.core.last_spoken import set_last_spoken
from jarvis.core.request_context import (
    correlation_id,
    ms_since_turn_start,
    new_correlation_id,
    turn_started_at,
)
from jarvis.core.state_machine import ConversationalState, Mode, StateMachine
from jarvis.security.origin_tracker import OriginTracker, RequestOrigin, set_request_context

if TYPE_CHECKING:
    from jarvis.audio.tts_manager import TTSManager
    from jarvis.core.phrases import Lang

log = logging.getLogger(__name__)

# T3.4: filler phrases spoken while the LLM thinks (module-level constant).
# M2-3: failures in a row after which a module is reported to the user (and
# the wake word switched off). Per frame that is ~0.1 s, so a transient
# glitch does not trip it, a broken module does at once.
MODULE_FAILURE_LIMIT = 5

_THINKING_KEYS = [
    "thinking_general_1", "thinking_general_2", "thinking_general_3",
    "thinking_check_1", "thinking_check_2",
]

# Default Q_frames depth: ~1 second of audio at 33 fps. Sized for jitter
# absorption; sustained drops indicate the loop is stalling and the warn
# log will surface that.
_FRAME_QUEUE_DEFAULT = 32

# See module docstring. Disabled by default: speaker-to-mic feedback fires
# spurious VAD SPEECH_STARTED during TTS playback and the system interrupts
# itself. Wake-word retrigger is the supported interrupt mechanism until AEC
# (or headphone-only deployment) is in place.
BARGE_IN_ENABLED: bool = False


# Time-of-day greeting: re-greet after this many seconds of silence.
_GREETING_COOLDOWN_S: float = 3600.0  # 1 hour

# Proactive speech (timers, reminders, announcements) that arrives while the
# user is mid-turn waits for IDLE instead of being dropped; "your timer is
# done" an hour late is noise, so old entries expire.
PROACTIVE_MAX_AGE_S: float = 600.0
PROACTIVE_QUEUE_MAX: int = 10


# ---------------------------------------------------------------------------
# Fire-and-forget task safety
# ---------------------------------------------------------------------------

def _task_exception_handler(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "Background task %s crashed: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )


# Letters that exist in Ukrainian but not in Russian. A transcript written
# with them is Ukrainian whatever Whisper's (renormalised) probability says.
_UK_ONLY_LETTERS = re.compile(r"[іїєґ]", re.IGNORECASE)


def _language_switch_reason(
    *,
    detected_language: str | None,
    language_probability: float,
    text_len: int,
    min_probability: float,
    min_chars: int,
    text: str = "",
) -> str | None:
    """Decide whether the reply voice may switch to *detected_language*.

    Task 7.1: Whisper can mis-detect the language on short/noisy utterances
    (e.g. a 4-char word at p=0.34), which would pointlessly reload a
    different voice (2-3 s) and reply in the wrong language. Pure function so
    the policy is unit-testable without building a pipeline.

    Returns None when the switch is allowed. Otherwise returns a short reason
    string ("no-language" / "low-probability" / "short-text") for a DEBUG log.
    The caller still skips the switch when the language equals the current
    one — that is the TTS manager's job, not this floor check.
    """
    if not detected_language:
        return "no-language"
    if language_probability < min_probability and not (
        detected_language == "uk" and _UK_ONLY_LETTERS.search(text)
    ):
        # Exception: a Ukrainian transcript that uses Ukrainian-only letters
        # (live 2026-11-02: uk=0.33 vs ru=0.21 -> p=0.60 < 0.70, the voice
        # stayed English for a Ukrainian reply).
        return "low-probability"
    if text_len < min_chars:
        return "short-text"
    return None


class AudioPipeline:
    def __init__(
        self,
        source: AudioSource,
        wake_word: WakeWordDetector,
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        tts: TextToSpeech,
        response_producer: ResponseProducer,
        bus: EventBus,
        sm: StateMachine,
        *,
        tts_manager: TTSManager | None = None,
        listening_timeout_seconds: float = LISTENING_TIMEOUT_DEFAULT,
        frame_queue_maxsize: int = _FRAME_QUEUE_DEFAULT,
        post_wake_blackout_ms: int = POST_WAKE_BLACKOUT_MS,
        min_utterance_ms: int = MIN_UTTERANCE_MS,
        barge_in_enabled: bool = BARGE_IN_ENABLED,
        on_wake: Callable[[], None] | None = None,
        log_wake_during_speaking: bool = False,
        # T3.3/T3.4 Phase 3 settings
        thinking_phrases_enabled: bool = True,
        half_duplex: bool = True,
        post_tts_pause_ms: int = 0,
        stt_timeout_s: float = 10.0,
        llm_timeout_s: float = 20.0,
        tts_timeout_s: float = 15.0,
        language_switch_min_probability: float = 0.70,
        language_switch_min_chars: int = 8,
        barge_in_manager: BargeInManager | None = None,
        sound_effects: SoundEffects | None = None,
    ) -> None:
        # T3.3: AEC / barge-in manager (optional — backwards compatible).
        self._source = source
        self._wake_word = wake_word
        self._vad = vad
        self._stt = stt
        self._tts = tts
        self._response_producer = response_producer
        self._bus = bus
        self._sm = sm
        self._tts_manager = tts_manager  # Phase 3: multilingual voice switching
        self._listening_timeout_seconds = listening_timeout_seconds
        self._post_wake_blackout_ms = post_wake_blackout_ms
        self._min_utterance_ms = min_utterance_ms
        self._barge_in_enabled = barge_in_enabled
        # Fires once per wake detection, before LISTENING transition.
        # The composition root wires Conversation.clear here so every
        # wake-word activation starts with empty history — the LLM
        # cannot anchor on prior turns to fire the wrong tool, at the
        # cost of breaking "tell me more" follow-ups. The trade-off is
        # documented in conversation.py.
        self._on_wake = on_wake
        self._log_wake_during_speaking = log_wake_during_speaking
        self._speaking_debug_frame_count: int = 0
        # T3.3/T3.4 Phase 3 state
        self._thinking_phrases_enabled = thinking_phrases_enabled
        self._half_duplex = half_duplex
        # Task 12: post-TTS wake-word suppression window (ms). Wake triggers
        # in IDLE within this window after TTS playback ends are treated as
        # self-trigger feedback and dropped (logged). Interrupt-by-voice
        # during SPEAKING/THINKING is never suppressed.
        self._post_tts_pause_ms = post_tts_pause_ms
        # Task 7.1: floors below which a detected language is NOT trusted
        # enough to switch the reply voice (avoids spurious 2-3 s reloads).
        self._language_switch_min_probability = language_switch_min_probability
        self._language_switch_min_chars = language_switch_min_chars
        # T3.3: BargeInManager — when provided, replaces the boolean
        # half_duplex flag with mode-aware echo management. If AEC is
        # available via the manager, override barge_in and half_duplex
        # so the pipeline uses the manager instead of the legacy flag.
        self._barge_in_manager = barge_in_manager
        self._sound_effects = sound_effects
        if barge_in_manager is not None and barge_in_manager.mode is BargeInMode.AEC:
            self._barge_in_enabled = True
            self._half_duplex = False
        # Barge-in requires SPEAKING-state frames to reach VAD, but
        # half_duplex drops ALL frames during SPEAKING. Force it off
        # whenever barge-in is enabled (either via constructor arg or AEC).
        if self._barge_in_enabled:
            self._half_duplex = False
        self._stt_timeout_s = stt_timeout_s
        self._llm_timeout_s = llm_timeout_s
        self._tts_timeout_s = tts_timeout_s

        self._q_frames: asyncio.Queue[AudioFrame] = asyncio.Queue(
            maxsize=frame_queue_maxsize
        )
        self._listen_buffer = bytearray()
        self._tts_started_at: float | None = None
        # Task 12: monotonic timestamp of the last TTS-playback end. Used
        # both for the post-TTS wake suppression window and for the
        # "ms since last TTS" field in the wake-trigger diagnostic log.
        self._tts_ended_at: float | None = None
        # Decremented per LISTENING-state frame; while > 0, frames are
        # discarded before VAD or buffer. Set on wake_word detection.
        self._blackout_frames_remaining: int = 0

        self._frame_task: asyncio.Task | None = None
        self._response_task: asyncio.Task | None = None
        self._listening_timer: asyncio.Task | None = None
        # Security Layer 1: per-session HMAC origin tracker.
        # Signs every STT transcription so downstream can prove user origin.
        self._origin_tracker = OriginTracker()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

        # Time-of-day greeting: timestamp of the last greeting spoken.
        self._last_greeting_time: float = time.monotonic()

        # Unsubscribe handles for ProactiveSpeech and the state events that
        # release queued proactive speech.
        self._unsub_proactive: Callable[[], None] | None = None
        self._unsub_proactive_release: list[Callable[[], None]] = []
        # (text, monotonic time queued), oldest first.
        self._proactive_queue: list[tuple[str, float]] = []
        self._proactive_speaking = False

        # M2-3 (T12.3): a module that keeps failing is reported, not left to
        # crash the frame loop (which then restarted on every frame, logged a
        # traceback each time, and stayed deaf). Consecutive failures per
        # module; the wake word is switched off after MODULE_FAILURE_LIMIT.
        self._module_failures: dict[str, int] = {}
        self._modules_reported: set[str] = set()
        self._wake_word_off = False
        # Language of the last conversation, for notices spoken outside a
        # turn (there is no request text then).
        self._last_language: str | None = None

    # -- module failures (M2-3) --

    def _module_ok(self, module: str) -> None:
        if self._module_failures.get(module):
            log.info("[module] %s works again", module)
            self._module_failures[module] = 0

    def _module_failed(self, module: str, exc: BaseException) -> int:
        """Count a failure of *module*; log the traceback once per streak.
        Returns the number of failures in a row."""
        count = self._module_failures.get(module, 0) + 1
        self._module_failures[module] = count
        if count == 1:
            log.error("[module] %s failed", module, exc_info=exc)
        else:
            log.debug("[module] %s failed again (%d in a row): %s", module, count, exc)
        if count >= MODULE_FAILURE_LIMIT and module not in self._modules_reported:
            self._modules_reported.add(module)
            self._report_module_down(module, exc)
        return count

    def _report_module_down(self, module: str, exc: BaseException) -> None:
        """Tell the user once: a tray notice (NonFatalError, works even when
        TTS is the broken module) and, if TTS works, a spoken one."""
        from jarvis.core.phrases import say

        if module == "wake_word":
            self._wake_word_off = True
        log.error(
            "[module] %s failed %d times in a row; %s",
            module, MODULE_FAILURE_LIMIT,
            "switched off" if module == "wake_word" else "reporting it",
        )
        self._bus.publish(NonFatalError(
            module=module,
            issue="module_failed",
            expected="working",
            actual=f"{type(exc).__name__}: {exc}"[:200],
        ))
        self._bus.publish(ProactiveSpeech(
            text=say(f"module_down_{module}", self._spoken_language())
        ))

    # -- lifecycle --

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._unsub_proactive = self._bus.subscribe(
            ProactiveSpeech, self._handle_proactive_speech)
        # Queued proactive speech is released when Jarvis is free again. The
        # handler re-reads the live state before speaking, so the async
        # dispatch of these events cannot make it talk over a new turn.
        self._unsub_proactive_release = [
            self._bus.subscribe(ConversationalStateChanged, self._on_cs_for_proactive),
            self._bus.subscribe(ModeChanged, self._on_mode_for_proactive),
        ]
        # Note: we deliberately do not subscribe to ConversationalStateChanged
        # for timer management. The pipeline is the single CS-mutator (per
        # design note) so timer start/cancel is wired directly into the
        # transition handlers below; subscriber-based wiring would race with
        # the synchronous SM state update vs. async bus dispatch.
        await self._source.start(self._on_audio_frame)
        self._frame_task = asyncio.create_task(
            self._frame_loop(), name="pipeline-frame-loop",
        )
        self._frame_task.add_done_callback(self._restart_frame_loop_on_crash)
        self._started = True

    def _restart_frame_loop_on_crash(self, task: asyncio.Task) -> None:
        """Auto-restart the frame loop if it crashes unexpectedly.

        The frame loop is critical: without it JARVIS is deaf.  A crash
        that isn't a deliberate cancellation (i.e. ``stop()`` was NOT
        called) is logged and the loop is immediately restarted so the
        user never notices a hang.
        """
        if task.cancelled() or not self._started:
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "frame loop crashed — auto-restarting: %s", exc, exc_info=exc,
            )
            self._frame_task = asyncio.create_task(
                self._frame_loop(), name="pipeline-frame-loop",
            )
            self._frame_task.add_done_callback(self._restart_frame_loop_on_crash)

    async def stop(self) -> None:
        if not self._started:
            return
        if self._unsub_proactive is not None:
            self._unsub_proactive()
            self._unsub_proactive = None
        for unsub in self._unsub_proactive_release:
            unsub()
        self._unsub_proactive_release = []
        self._proactive_queue.clear()
        self._started = False
        try:
            await self._source.stop()
        except Exception:
            log.exception("audio source stop failed")
        await self._cancel_response_chain()
        self._cancel_listening_timer()
        if self._frame_task is not None:
            self._frame_task.cancel()
            try:
                await self._frame_task
            except asyncio.CancelledError:  # noqa: S110
                # Expected: we just cancelled this task and awaited it to
                # observe the cancellation. Not an error — nothing to log.
                pass
            self._frame_task = None

    # -- source-thread -> loop bridge --

    def _on_audio_frame(self, frame: AudioFrame) -> None:
        # Called from the audio source thread (PortAudio in production, the
        # test harness in tests). Hand off to the loop without blocking.
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._enqueue_frame, frame)

    def _enqueue_frame(self, frame: AudioFrame) -> None:
        # Cheap ingress assertion: misconfigured sources surface immediately.
        if len(frame) != FRAME_BYTES:
            log.error(
                "audio source produced wrong frame size: %d bytes (expected %d)",
                len(frame), FRAME_BYTES,
            )
            return
        try:
            self._q_frames.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._q_frames.get_nowait()  # drop oldest
                self._q_frames.put_nowait(frame)
                log.warning("Q_frames full; dropped oldest frame")
            except asyncio.QueueEmpty:  # noqa: S110
                # Race: someone drained between put_nowait and get_nowait.
                pass

    # -- listening silence timeout --

    def _start_listening_timer(self) -> None:
        self._cancel_listening_timer()
        self._listening_timer = asyncio.create_task(
            self._listening_timeout_coro(), name="pipeline-listening-timeout",
        )
        self._listening_timer.add_done_callback(_task_exception_handler)

    def _cancel_listening_timer(self) -> None:
        if self._listening_timer is not None:
            self._listening_timer.cancel()
            self._listening_timer = None

    async def _listening_timeout_coro(self) -> None:
        try:
            await asyncio.sleep(self._listening_timeout_seconds)
        except asyncio.CancelledError:
            return
        # Re-check: another transition may have moved us out of LISTENING
        # in the gap between sleep wake and this line.
        if self._sm.conversational_state is ConversationalState.LISTENING:
            log.info("listening silence timeout; returning to IDLE")
            self._sm.set_conversational_state(ConversationalState.IDLE)

    # -- frame loop --

    async def _frame_loop(self) -> None:
        try:
            while True:
                frame = await self._q_frames.get()
                if self._sm.mode is not Mode.ACTIVE:
                    continue
                # Feed wake word for ALL states (IDLE, LISTENING, THINKING,
                # SPEAKING). During SPEAKING/THINKING this enables the
                # wake-word interrupt path: if the user says "hey jarvis"
                # mid-response, _handle_wake_detection cancels TTS and
                # returns to LISTENING. openWakeWord's narrow phoneme set
                # means TTS output won't self-trigger it.
                wake_result = None
                if not self._wake_word_off:
                    try:
                        wake_result = await self._wake_word.feed(frame)
                    except Exception as exc:
                        self._module_failed("wake_word", exc)
                    else:
                        self._module_ok("wake_word")
                if wake_result is not None:
                    cs_at_trigger = self._sm.conversational_state
                    ms_since_tts = self._ms_since_last_tts()
                    # Task 12 pt 1: one INFO line on EVERY wake trigger so
                    # false fires are diagnosable from the log alone. No
                    # audio, no transcript -- just the decision inputs.
                    log.info(
                        "[wake] trigger score=%.4f threshold=%.4f "
                        "sensitivity=%.2f ms_since_tts=%s state=%s",
                        getattr(self._wake_word, "last_score", 0.0),
                        getattr(self._wake_word, "threshold", float("nan")),
                        getattr(self._wake_word, "sensitivity", float("nan")),
                        "n/a" if ms_since_tts is None else f"{ms_since_tts:.0f}",
                        cs_at_trigger.name,
                    )
                    # Task 12 pt 2: post-TTS self-trigger suppression. Only
                    # in IDLE -- an in-conversation interrupt (SPEAKING/
                    # THINKING) must always be honoured. Drops triggers that
                    # land on the speaker-to-mic feedback tail of our own
                    # speech within the configured window.
                    if (
                        cs_at_trigger is ConversationalState.IDLE
                        and self._post_tts_pause_ms > 0
                        and ms_since_tts is not None
                        and ms_since_tts < self._post_tts_pause_ms
                    ):
                        log.info(
                            "[wake] suppressed: within %d ms post-TTS window "
                            "(ms_since_tts=%.0f)",
                            self._post_tts_pause_ms, ms_since_tts,
                        )
                    else:
                        await self._handle_wake_detection(wake_result)
                    continue
                cs = self._sm.conversational_state
                if self._log_wake_during_speaking and cs is ConversationalState.SPEAKING:
                    self._speaking_debug_frame_count += 1
                    if self._speaking_debug_frame_count % 10 == 0:
                        log.debug(
                            "[wake-during-speaking] score=%.4f threshold=%.4f",
                            getattr(self._wake_word, "last_score", 0.0),
                            getattr(self._wake_word, "threshold", float("nan")),
                        )
                try:
                    if cs is ConversationalState.LISTENING:
                        await self._dispatch_listening(frame)
                    elif cs is ConversationalState.THINKING:
                        # Run VAD so its internal state stays warm; do not act.
                        await self._vad.feed(frame)
                    elif cs is ConversationalState.SPEAKING:
                        await self._dispatch_speaking(frame)
                    # IDLE: nothing to do beyond the wake-word feed already done.
                except Exception as exc:
                    await self._on_vad_failure(exc, cs)
                else:
                    if cs is not ConversationalState.IDLE:
                        self._module_ok("vad")
        except asyncio.CancelledError:  # noqa: S110
            # Normal shutdown of the frame loop; re-raising is unnecessary
            # here because the task is being torn down. Not an error.
            pass
        except Exception:
            log.exception("frame loop crashed")
            raise

    async def _on_vad_failure(self, exc: Exception, cs: ConversationalState) -> None:
        """VAD (end-of-speech detection) raised on a frame. While listening,
        the turn cannot end normally, so it is dropped back to IDLE."""
        self._module_failed("vad", exc)
        if cs is ConversationalState.LISTENING:
            self._cancel_listening_timer()
            self._listen_buffer.clear()
            self._sm.set_conversational_state(ConversationalState.IDLE)

    def _spoken_language(self) -> Lang | None:
        """Language for notices outside a turn: the last conversation's,
        else STT's configured one, else None (English)."""
        lang = self._last_language or getattr(self._stt, "language", None)
        if lang == "ru" or lang == "uk" or lang == "en":
            return lang
        return None

    async def _dispatch_listening(self, frame: AudioFrame) -> None:
        # Post-wake blackout: discard the first ~POST_WAKE_BLACKOUT_MS of
        # frames so the trailing tail of "jarvis" doesn't pollute VAD or
        # the listen buffer.
        if self._blackout_frames_remaining > 0:
            self._blackout_frames_remaining -= 1
            return
        self._listen_buffer.extend(frame)
        event = await self._vad.feed(frame)
        if event is VADEvent.SPEECH_STARTED:
            # User is talking; cancel the silence deadline.
            self._cancel_listening_timer()
        elif event is VADEvent.ENDPOINT:
            await self._handle_endpoint()

    async def _dispatch_speaking(self, frame: AudioFrame) -> None:
        # Gate at the top: when barge-in is disabled there's no reason to
        # spend VAD CPU on SPEAKING-state frames at all. The VAD's internal
        # state will be reset on the next wake detection, so skipping doesn't
        # leave it in a confused state.
        # T3.3: Half-duplex mode — drop ALL audio frames during SPEAKING to
        # prevent speaker→mic feedback from triggering false VAD events.
        # Wake-word detection still runs (in _frame_loop before this method)
        # so "Hey Jarvis" interrupt works even in half-duplex mode.
        if self._half_duplex:
            return
        # T3.3: AEC-aware path — if a BargeInManager with AEC is active,
        # run echo cancellation on the frame before feeding VAD / barge-in.
        if self._barge_in_manager is not None:
            processed = self._barge_in_manager.process_audio(frame)
            if self._barge_in_manager.check_barge_in(processed):
                await self._handle_barge_in()
                return
            # Even with AEC, if barge_in_enabled is False we don't feed VAD.
            if not self._barge_in_enabled:
                return
            event = await self._vad.feed(processed)
            if event is VADEvent.SPEECH_STARTED:
                await self._handle_barge_in()
            return
        if not self._barge_in_enabled:
            return
        event = await self._vad.feed(frame)
        if event is VADEvent.SPEECH_STARTED:
            await self._handle_barge_in()

    # -- transition handlers --

    def _ms_since_last_tts(self) -> float | None:
        """Milliseconds since TTS playback last ended, or None if TTS has
        not played yet (or is still playing right now).

        Returns None while ``_tts_started_at`` is set (TTS in progress) so
        the post-TTS window never applies during active speech. Used by the
        wake diagnostic log and the post-TTS suppression gate (Task 12)."""
        if self._tts_started_at is not None:
            return None
        if self._tts_ended_at is None:
            return None
        delta = (time.monotonic() - self._tts_ended_at) * 1000.0
        # Clamp tiny negative drift from monotonic rounding to 0.
        return max(0.0, delta)

    async def _handle_wake_detection(self, result: WakeWordResult) -> None:
        # NOTE: this branch is reached only when the wake-word model fires
        # during SPEAKING/THINKING. On speaker-based setups without AEC,
        # speaker bleed masks the user's voice and the model never fires —
        # see the "Known limitation" section in the module docstring.
        # With headphones (or once Phase 6 AEC lands) this path works correctly.
        cs = self._sm.conversational_state
        if cs is ConversationalState.SPEAKING or cs is ConversationalState.THINKING:
            log.info("[wake] interrupted %s", cs.name)
            await self._cancel_response_chain()
            # THINKING → LISTENING is an illegal transition; pass through IDLE.
            # SPEAKING → LISTENING is legal so no detour is needed there.
            if self._sm.conversational_state is ConversationalState.THINKING:
                self._sm.set_conversational_state(ConversationalState.IDLE)
        self._bus.publish(WakeWordDetected(confidence=result.confidence))
        # Auditory confirmation: short beep so the user knows Jarvis heard.
        if self._sound_effects is not None:
            self._sound_effects.play_wake()
        # Time-of-day greeting: speak once on first wake or after a long gap.
        now = time.monotonic()
        if now - self._last_greeting_time >= _GREETING_COOLDOWN_S:
            self._last_greeting_time = now
            try:
                from datetime import datetime

                from jarvis.core.phrases import say
                hour = datetime.now().hour
                if 5 <= hour < 12:
                    key = "good_morning"
                elif 12 <= hour < 18:
                    key = "good_afternoon"
                else:
                    key = "good_evening"
                greeting = say(key)
                await self._tts.speak(greeting)
                log.info("[wake] time-of-day greeting: %s", key)
            except Exception:
                log.warning("[wake] greeting failed", exc_info=True)
        self._listen_buffer.clear()
        self._wake_word.reset()
        self._vad.reset()
        # Wipe conversation history (when the composition root wired it).
        # Isolates each wake-word activation: prior tool-anchored context
        # can't bias the LLM into firing the wrong tool on a fresh
        # request. Failure is swallowed-with-log because a busted hook
        # must not stop the listening transition.
        if self._on_wake is not None:
            try:
                self._on_wake()
            except Exception:
                log.exception("on_wake hook raised; ignoring")
        # Arm the post-wake blackout. Ceiling division so the actual
        # blackout is always >= the configured ms.
        if self._post_wake_blackout_ms > 0:
            self._blackout_frames_remaining = (
                -(-self._post_wake_blackout_ms // FRAME_DURATION_MS)
            )
        else:
            self._blackout_frames_remaining = 0
        self._sm.set_conversational_state(ConversationalState.LISTENING)
        self._start_listening_timer()

    async def _handle_endpoint(self) -> None:
        self._cancel_listening_timer()
        audio = bytes(self._listen_buffer)
        self._listen_buffer.clear()
        # Bug-D gate: don't burn an STT call on near-silence. Buffer is
        # post-blackout, so this measures actual captured audio length.
        audio_ms = (len(audio) // 2) * 1000 // SAMPLE_RATE
        if audio_ms < self._min_utterance_ms:
            log.info(
                "utterance too short (%d ms < %d ms); returning to IDLE",
                audio_ms, self._min_utterance_ms,
            )
            self._sm.set_conversational_state(ConversationalState.IDLE)
            return
        self._sm.set_conversational_state(ConversationalState.THINKING)
        # The response chain runs as a separate task so the frame loop keeps
        # processing barge-in candidates while STT/LLM/TTS execute.
        self._response_task = asyncio.create_task(
            self._run_response_chain(audio), name="pipeline-response-chain",
        )
        self._response_task.add_done_callback(_task_exception_handler)

    async def _handle_barge_in(self) -> None:
        # Re-check CS at decision time. VAD events can land here after the
        # response chain has already finished and the SM moved SPEAKING->IDLE,
        # because _dispatch_speaking sees a stale state value between when the
        # frame was dequeued and when this coroutine actually runs. Without
        # this guard, a delayed SPEECH_STARTED fires barge-in from IDLE,
        # cancelling nothing and pulling the SM into LISTENING — exactly the
        # spurious "barge_in elapsed_ms=1953" trace from the live log.
        if self._sm.conversational_state is not ConversationalState.SPEAKING:
            return
        # Suppress within the grace window after TTS playback start.
        # Mitigates speaker->mic feedback false positives.
        if self._tts_started_at is not None:
            elapsed = time.monotonic() - self._tts_started_at
            if elapsed < TTS_BARGE_IN_GRACE_SECONDS:
                return
            elapsed_ms = int(elapsed * 1000)
        else:
            # Defensive: SPEAKING reached without _tts_started_at set
            # shouldn't happen, but if it does we log it visibly.
            elapsed_ms = -1
        log.info("[barge-in] triggered after %dms of TTS playback", elapsed_ms)
        await self._cancel_response_chain()
        self._listen_buffer.clear()
        self._vad.reset()
        # SPEAKING -> LISTENING: legal, and the user clearly wants to talk.
        self._sm.set_conversational_state(ConversationalState.LISTENING)
        self._start_listening_timer()

    # -- proactive speech (timer/reminder callbacks) --

    async def _handle_proactive_speech(self, event: ProactiveSpeech) -> None:
        """Speak *event.text* outside the normal voice-turn flow.

        Called by the EventBus when a ``ProactiveSpeech`` event is published
        (e.g. timer expiry, scheduled reminder). It never interrupts: while
        the user is mid-turn (or Jarvis is muted / asleep) the text waits in
        a queue and is spoken once Jarvis is IDLE and ACTIVE again. Dropping
        it, as before, lost a timer that fired while Jarvis was answering.
        """
        if not self._started:
            return
        if any(text == event.text for text, _ in self._proactive_queue):
            return
        if len(self._proactive_queue) >= PROACTIVE_QUEUE_MAX:
            dropped, _ = self._proactive_queue.pop(0)
            log.info("[proactive] queue full; dropping oldest: %s", dropped[:80])
        self._proactive_queue.append((event.text, time.monotonic()))
        await self._speak_queued_proactive()

    async def _on_cs_for_proactive(self, event: ConversationalStateChanged) -> None:
        if event.new is ConversationalState.IDLE:
            await self._speak_queued_proactive()

    async def _on_mode_for_proactive(self, event: ModeChanged) -> None:
        if event.new is Mode.ACTIVE:
            await self._speak_queued_proactive()

    async def _speak_queued_proactive(self) -> None:
        """Speak queued proactive texts while Jarvis is free, oldest first."""
        if self._proactive_speaking:
            return  # the running call picks up whatever is queued
        self._proactive_speaking = True
        try:
            while self._started and self._proactive_queue:
                now = time.monotonic()
                while self._proactive_queue and (
                    now - self._proactive_queue[0][1] > PROACTIVE_MAX_AGE_S
                ):
                    stale, _ = self._proactive_queue.pop(0)
                    log.info("[proactive] expired before Jarvis was free: %s", stale[:80])
                if not self._proactive_queue:
                    return
                cs = self._sm.conversational_state
                if self._sm.mode is not Mode.ACTIVE or cs is not ConversationalState.IDLE:
                    log.info(
                        "[proactive] queued while %s/%s: %s",
                        self._sm.mode.name, cs.name, self._proactive_queue[-1][0][:80],
                    )
                    return
                text, _ = self._proactive_queue.pop(0)
                await self._speak_proactive_now(text)
        finally:
            self._proactive_speaking = False

    async def _speak_proactive_now(self, text: str) -> None:
        log.info("[proactive] speaking: %s", text[:80])
        try:
            # SPEC's transition table only leaves IDLE for LISTENING, so going
            # to SPEAKING directly raised IllegalTransition and every timer,
            # reminder and background announcement was dropped unspoken.
            # Walk the legal path. The three steps run with no await in
            # between, so the frame loop never sees LISTENING and records
            # nothing; only the proactive queue listens to CS changes.
            for state in (
                ConversationalState.LISTENING,
                ConversationalState.THINKING,
                ConversationalState.SPEAKING,
            ):
                self._sm.set_conversational_state(state)
            await self._tts.speak(text)
        except Exception:
            log.exception("[proactive] TTS failed")
        finally:
            if self._sm.conversational_state is not ConversationalState.IDLE:
                self._sm.set_conversational_state(ConversationalState.IDLE)

    # -- response chain --

    async def _run_response_chain(self, audio: bytes) -> None:
        # T1.2: one correlation id per voice turn. This coroutine runs in its
        # own task (see _handle_endpoint), so the id lives only in this turn's
        # context and is inherited by STT threads, the LLM call, tools and TTS.
        cid_token = correlation_id.set(new_correlation_id())
        start_token = turn_started_at.set(time.monotonic())
        log.info(
            "[turn] start: %d ms of audio",
            (len(audio) // 2) * 1000 // SAMPLE_RATE,
        )
        # The request, once transcribed: an error notice is said in its
        # language (the producer's request context is gone by then).
        text: str | None = None
        try:
            # T3.4: STT with timeout — if transcription takes too long, abort.
            try:
                if self._stt_timeout_s > 0:
                    result = await asyncio.wait_for(
                        self._stt.transcribe(audio), timeout=self._stt_timeout_s
                    )
                else:
                    result = await self._stt.transcribe(audio)
            except TimeoutError:
                log.warning("[stt] timeout after %.1f s", self._stt_timeout_s)
                from jarvis.core.phrases import say
                await self._tts.speak(say("error_stt_timeout"))
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return
            except Exception as exc:
                # M2-3: say what failed instead of the generic error.
                self._module_failed("stt", exc)
                from jarvis.core.phrases import say
                await self._tts.speak(say("error_stt_failed", self._spoken_language()))
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return
            self._module_ok("stt")
            if result.detected_language:
                self._last_language = result.detected_language
            text = result.text
            # Security Layer 1: cryptographically sign the STT output so
            # the IntentRouter (and ultimately ToolGuard) can distinguish
            # a real user utterance from an LLM-fabricated instruction.
            _sec_token = self._origin_tracker.sign_user_input(text)
            set_request_context(self._origin_tracker.create_context(
                RequestOrigin.USER_VOICE, original_text=text, token=_sec_token,
            ))
            # T2.4: Log confidence and detected language.
            log.info(
                "[stt] done at +%s ms: %d chars, confidence=%.2f, action=%s, lang=%s",
                ms_since_turn_start(),
                len(text),
                result.confidence,
                result.action,
                result.detected_language or "?",
            )
            duration_ms = (len(audio) // 2) * 1000 // SAMPLE_RATE
            self._bus.publish(TranscriptionReady(text=text, duration_ms=duration_ms))

            if result.is_empty:
                # No speech; abort cleanly back to IDLE.
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return

            # T2.4: Confidence-based action routing.
            if result.action == "ignore":
                log.info(
                    "[stt] confidence %.2f below ignore threshold; dropping",
                    result.confidence,
                )
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return

            if result.action == "clarify":
                log.info(
                    "[stt] confidence %.2f in clarify range; asking to repeat",
                    result.confidence,
                )
                # Speak a clarification phrase and return to IDLE.
                # The user will re-trigger via wake word.
                from jarvis.core.phrases import say
                await self._tts.speak(say("error_stt"))
                # No speech; abort cleanly back to IDLE.
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return

            # Phase 3: Switch TTS voice to match detected language.
            # This must happen BEFORE thinking phrase or response speak,
            # so JARVIS replies in the same language the user spoke.
            # Task 7.1: only trust the detected language when BOTH the
            # language probability and the transcript length clear the
            # configured floors (see _language_switch_reason). A 4-char
            # utterance at p=0.34 would otherwise reload a different voice
            # (2-3 s) and reply in the wrong language.
            if self._tts_manager is not None:
                _skip = _language_switch_reason(
                    detected_language=result.detected_language,
                    language_probability=result.language_probability,
                    text_len=len(text),
                    min_probability=self._language_switch_min_probability,
                    min_chars=self._language_switch_min_chars,
                    text=text,
                )
                if _skip is not None:
                    log.debug(
                        "[tts] keeping voice (%s): lang=%r p=%.2f chars=%d",
                        _skip, result.detected_language,
                        result.language_probability, len(text),
                    )
                elif result.detected_language is not None:
                    # (_skip is "no-language" when it is None.)
                    try:
                        await self._tts_manager.set_language(
                            result.detected_language
                        )
                    except Exception:
                        log.warning(
                            "tts_manager.set_language failed", exc_info=True
                        )

            # T3.4: Thinking phrase — speak a short filler via Piper (CPU)
            # so the user doesn't hear silence while the LLM thinks.
            if self._thinking_phrases_enabled:
                from jarvis.core.phrases import say
                phrase_key = random.choice(_THINKING_KEYS)
                await self._tts.speak(say(phrase_key))
                log.debug("[thinking] spoke phrase %r", phrase_key)

            # Tee the producer into speak_stream. Each chunk yielded by
            # the producer is recorded for LLMResponseChunk emission and
            # full-text assembly, then handed through to PiperTTS.
            # speak_stream which buffers up to a sentence boundary
            # internally -- avoids the per-token synthesis stutter that
            # `for chunk: await tts.speak(chunk)` would produce on
            # streaming LLM responses. Cancellation propagates through
            # the iterator the same way: a cancel on this task raises
            # CancelledError inside speak_stream's __anext__, which
            # cascades into the producer (-> ollama -> httpx context
            # manager close).
            full_text_parts: list[str] = []
            first_chunk_seen = False

            async def teed_chunks():
                nonlocal first_chunk_seen
                async for chunk in self._response_producer(text):
                    if not first_chunk_seen:
                        log.info(
                            "[llm] first chunk at +%s ms", ms_since_turn_start()
                        )
                        self._sm.set_conversational_state(
                            ConversationalState.SPEAKING
                        )
                        self._tts_started_at = time.monotonic()
                        # T3.3: notify barge-in manager that TTS is active.
                        if self._barge_in_manager is not None:
                            self._barge_in_manager.on_tts_start()
                        first_chunk_seen = True
                    full_text_parts.append(chunk)
                    self._bus.publish(LLMResponseChunk(text=chunk))
                    yield chunk

            await self._tts.speak_stream(teed_chunks())

            if not first_chunk_seen:
                # Producer yielded nothing; nothing to speak.
                self._sm.set_conversational_state(ConversationalState.IDLE)
                return

            full_text = "".join(full_text_parts)
            # Capture the substantive response for "repeat that" / "повтори".
            set_last_spoken(full_text)
            self._bus.publish(
                LLMResponseComplete(full_text=full_text)
            )
            # Optional auditory confirmation: short ding after a full turn.
            if self._sound_effects is not None:
                self._sound_effects.play_success()
            self._sm.set_conversational_state(ConversationalState.IDLE)
        except asyncio.CancelledError:
            # Barge-in or shutdown. Re-raise so the outer awaiter sees it.
            raise
        except Exception as exc:
            # T1.5: unified error handling — log full traceback, speak a
            # user-friendly message via TTS, recover to IDLE.  The audio
            # pipeline is NOT restarted: only the current turn is aborted.
            log.exception("response chain crashed — recovering to IDLE")
            # Best-effort TTS notification so the user knows something broke:
            # the error's own phrase when it has one (Ollama not running, the
            # model not pulled), else say("error_generic").
            try:
                from jarvis.core.phrases import PHRASES, detect_language, say
                key = getattr(exc, "spoken_key", "error_generic")
                lang = detect_language(text) or self._spoken_language()
                await self._tts.speak(say(key if key in PHRASES else "error_generic", lang))
            except Exception:
                log.warning("TTS error notification failed", exc_info=True)
            # Don't leave the state machine stuck in THINKING/SPEAKING.
            try:
                self._sm.set_conversational_state(ConversationalState.IDLE)
            except Exception:
                log.exception("recovery to IDLE failed")
            # Pipeline loop continues — the frame_loop and wake-word
            # detection are still running; only this turn's response
            # chain was affected.  No restart needed.
        finally:
            self._tts_started_at = None
            # Task 12: record when playback ended so the post-TTS wake
            # suppression window and the wake diagnostic can measure age.
            self._tts_ended_at = time.monotonic()
            # T3.3: notify barge-in manager that TTS is done.
            if self._barge_in_manager is not None:
                self._barge_in_manager.on_tts_end()
            log.info("[turn] end: total %s ms", ms_since_turn_start())
            turn_started_at.reset(start_token)
            correlation_id.reset(cid_token)

    async def _cancel_response_chain(self) -> None:
        # Order per design note: TTS first (user-perceptible), then drain
        # internal state, then cancel the producer task.
        try:
            await self._tts.cancel()
        except Exception:
            log.exception("tts.cancel failed during cancellation")
        if self._response_task is not None and not self._response_task.done():
            self._response_task.cancel()
            try:
                await self._response_task
            except asyncio.CancelledError:  # noqa: S110
                # Expected: we cancelled this task and awaited it to let the
                # cancellation settle. Not an error — nothing to log.
                pass
            except Exception:
                log.exception("response task raised during cancellation")
        self._response_task = None
