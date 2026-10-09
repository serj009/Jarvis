"""Acoustic Echo Cancellation (AEC) and barge-in management (T3.3).

Problem: when JARVIS speaks through speakers, the microphone picks up TTS
output → STT transcribes JARVIS's own voice → the system "interrupts itself".

Three operating modes (graceful degradation chain):

1. **HALF_DUPLEX** (default, reliable) — mute mic during SPEAKING state.
   User can still interrupt via wake-word (separate detector path).
2. **AEC** (advanced) — WebRTC APM filters TTS echo from mic input,
   enabling full-duplex conversation with real-time barge-in.
3. **HEADPHONES** (no-op) — headphones isolate speaker output from mic;
   no echo problem exists, mic stays open, barge-in works natively.

Fallback chain: AEC → HALF_DUPLEX (if no AEC library installed).

Dependencies:
  HALF_DUPLEX / HEADPHONES: none (built-in).
  AEC: ``pip install pywebrtc-audio`` (preferred) or ``pip install echoff``.
  Both are optional; missing library triggers automatic fallback with a
  warning — the system never fails to start.

Fully local — no cloud dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

from jarvis.audio.protocols import SAMPLE_RATE

log = logging.getLogger(__name__)


class BargeInMode(Enum):
    """Echo-cancellation / barge-in operating mode."""

    HALF_DUPLEX = "half_duplex"  # Mute mic while SPEAKING
    AEC = "aec"                  # WebRTC echo cancellation (full-duplex)
    HEADPHONES = "headphones"    # No echo problem — headphones isolate


class BargeInManager:
    """Manages barge-in (user interrupting JARVIS mid-speech).

    Lifecycle:
      Created by the composition root (_build_audio_stack) and passed to
      AudioPipeline. The pipeline calls on_tts_start / on_tts_end at
      SPEAKING transitions and process_audio / check_barge_in per frame.

    Half-duplex (default):
      - During SPEAKING: microphone muted via callback.
      - User can interrupt via wake-word (detected on a separate path
        that receives raw frames unconditionally).
      - On TTS end: mic unmuted automatically.

    AEC (advanced):
      - Microphone stays open at all times.
      - WebRTC APM subtracts the speaker reference signal from the mic
        input, yielding a clean voice-only stream.
      - VAD on the cleaned signal detects user speech → barge-in.

    Headphones:
      - No muting, no processing — headphones prevent echo inherently.
    """

    def __init__(
        self,
        *,
        mode: str = "half_duplex",
        on_mic_mute: Callable[[], None] | None = None,
        on_mic_unmute: Callable[[], None] | None = None,
        on_barge_in: Callable[[], None] | None = None,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._mode = BargeInMode(mode)
        self._on_mic_mute = on_mic_mute
        self._on_mic_unmute = on_mic_unmute
        self._on_barge_in = on_barge_in
        self._sample_rate = sample_rate

        self._mic_muted: bool = False
        self._tts_playing: bool = False
        self._aec_processor: object | None = None

        if self._mode is BargeInMode.AEC:
            self._init_aec()

        log.info("BargeInManager initialised: mode=%s", self._mode.value)

    # -- AEC initialisation --------------------------------------------------

    def _init_aec(self) -> None:
        """Try to load a WebRTC AEC backend; fall back to HALF_DUPLEX."""
        try:
            from pywebrtc_audio import AudioProcessingModule  # type: ignore[import-untyped]

            self._aec_processor = AudioProcessingModule(
                enable_echo_cancellation=True,
                enable_noise_suppression=True,
                enable_auto_gain_control=True,
                enable_voice_activity_detection=True,
                sample_rate=self._sample_rate,
            )
            log.info("WebRTC AEC initialised (pywebrtc-audio)")
            return
        except ImportError:
            pass

        try:
            from echoff import EchoCanceller  # type: ignore[import-untyped]

            self._aec_processor = EchoCanceller(sample_rate=self._sample_rate)
            log.info("AEC initialised (echoff fallback)")
            return
        except ImportError:
            pass

        log.warning(
            "No AEC library found (install pywebrtc-audio or echoff). "
            "Falling back to half-duplex mode."
        )
        self._mode = BargeInMode.HALF_DUPLEX

    # -- Half-duplex TTS callbacks -------------------------------------------

    def on_tts_start(self) -> None:
        """Called when TTS begins playback."""
        self._tts_playing = True
        if self._mode is BargeInMode.HALF_DUPLEX:
            self._mute_mic()
            log.debug("TTS started → mic muted (half-duplex)")

    def on_tts_end(self) -> None:
        """Called when TTS finishes playback."""
        self._tts_playing = False
        if self._mode is BargeInMode.HALF_DUPLEX:
            self._unmute_mic()
            log.debug("TTS ended → mic unmuted (half-duplex)")

    def _mute_mic(self) -> None:
        if not self._mic_muted:
            self._mic_muted = True
            if self._on_mic_mute is not None:
                self._on_mic_mute()

    def _unmute_mic(self) -> None:
        if self._mic_muted:
            self._mic_muted = False
            if self._on_mic_unmute is not None:
                self._on_mic_unmute()

    # -- AEC audio processing ------------------------------------------------

    def process_audio(
        self,
        mic_chunk: bytes,
        speaker_chunk: bytes | None = None,
    ) -> bytes:
        """Filter echo from *mic_chunk* using *speaker_chunk* as reference.

        In AEC mode, the processor subtracts the speaker signal from the
        mic input. In other modes (or on error), the mic chunk is returned
        unchanged — a safe passthrough.

        Args:
            mic_chunk:     Raw PCM bytes from the microphone.
            speaker_chunk: Raw PCM bytes currently playing through speakers
                           (the echo reference). May be ``None`` when TTS is
                           not active.

        Returns:
            Cleaned mic audio (echo removed) or the original mic_chunk.
        """
        if self._mode is not BargeInMode.AEC or self._aec_processor is None:
            return mic_chunk

        try:
            proc = self._aec_processor
            if hasattr(proc, "process"):
                return proc.process(mic_chunk, speaker_chunk)  # type: ignore[union-attr]
            if hasattr(proc, "cancel_echo"):
                return proc.cancel_echo(mic_chunk, speaker_chunk)  # type: ignore[union-attr]
        except Exception:
            log.exception("AEC processing error; passing raw mic audio through")

        return mic_chunk

    # -- Barge-in detection --------------------------------------------------

    def check_barge_in(self, mic_chunk: bytes) -> bool:
        """Check whether the user is attempting to interrupt JARVIS.

        Only meaningful while TTS is playing. In half-duplex mode the
        wake-word detector (running outside this class) handles interrupt.
        In AEC mode, the processor's built-in VAD on the cleaned signal
        is checked.

        Returns:
            ``True`` if a barge-in is detected and the callback fired.
        """
        if not self._tts_playing:
            return False

        # AEC mode: check processor's built-in voice-activity flag.
        if self._mode is BargeInMode.AEC and self._aec_processor is not None:
            if hasattr(self._aec_processor, "has_voice"):
                if self._aec_processor.has_voice:  # type: ignore[union-attr]
                    log.info("Barge-in detected (AEC + VAD)")
                    self._trigger_barge_in()
                    return True

        return False

    def _trigger_barge_in(self) -> None:
        """Handle a confirmed barge-in: reset TTS state and notify."""
        self._tts_playing = False
        self._unmute_mic()
        if self._on_barge_in is not None:
            self._on_barge_in()

    # -- Introspection -------------------------------------------------------

    @property
    def mode(self) -> BargeInMode:
        """Current operating mode (may differ from requested if AEC fell back)."""
        return self._mode

    @property
    def is_mic_muted(self) -> bool:
        """Whether the microphone is currently muted by this manager."""
        return self._mic_muted

    @property
    def tts_playing(self) -> bool:
        """Whether TTS is currently playing (as reported by callbacks)."""
        return self._tts_playing

    @property
    def aec_available(self) -> bool:
        """Whether a hardware AEC processor was successfully loaded."""
        return self._aec_processor is not None

    def get_status(self) -> dict[str, object]:
        """Diagnostic snapshot for the dashboard / status API."""
        return {
            "mode": self._mode.value,
            "mic_muted": self._mic_muted,
            "tts_playing": self._tts_playing,
            "aec_available": self._aec_processor is not None,
        }
