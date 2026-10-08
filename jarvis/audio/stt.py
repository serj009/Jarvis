"""faster-whisper STT wrapper — multilingual with confidence scoring.

Implements SpeechToText (audio/protocols.py) and Loadable (core/lifecycle.py).

Phase 2 enhancements over the Phase 1 version:
- T2.1: Multilingual STT — whisper ``small`` recommended for UA/RU/EN.
         Language can be "auto" for auto-detection or a fixed ISO code.
- T2.2: Language-aware output — TranscriptionResult carries detected_language
         so the pipeline can route to the correct TTS voice and i18n phrases.
- T2.3: Hotwords for UA/RU/EN — brand names spelled in Cyrillic for each
         language so Whisper decodes them correctly regardless of input lang.
- T2.4: STT Confidence — each TranscriptionResult has a 0.0–1.0 confidence
         score derived from avg_logprob and no_speech_prob, plus an action
         field ("proceed" / "clarify" / "ignore") based on configurable
         thresholds.

Threading
---------
faster_whisper.WhisperModel.transcribe() is synchronous and CPU-bound.
A few-second utterance typically takes 200 ms - 2 s of CPU on a modern
desktop with the int8 base.en model. Calling it directly from the asyncio
loop would block the frame loop -- which freezes barge-in detection and
audio capture during exactly the moment the user might want to interrupt.

This wrapper runs every transcribe() call inside asyncio.to_thread so the
loop stays free. The frame loop continues to process VAD frames
concurrently with whisper inference. CRITICAL: do NOT short-circuit this
for "small" inputs. Even a 0.5 s clip can take 200 ms+; that's the same
order as our 30 ms frame cadence, so blocking would visibly chunk audio.

Model construction also goes through asyncio.to_thread, because the
WhisperModel(...) constructor downloads model files from HuggingFace on
first run -- a multi-second-to-multi-minute operation depending on
network speed and model size.

Model files
-----------
faster-whisper downloads quantized models from HuggingFace Hub on first
use, caching them at ~/.cache/huggingface/hub/ by default. The base.en
int8 model is ~150 MB; small is ~500 MB.

In production (post-Phase-7) the recommended approach is to pre-place
the model files into a known directory and pass `download_root` so the
runtime never needs network. Bundling ~500 MB inside the installer is
acceptable; the alternative (download-on-first-launch with a progress UI
in the first-launch wizard) is documented in BUILD.md Phase 7 Task 3 but
not chosen yet -- defer to Phase 7.

For development before the installer exists, the model downloads
transparently on the first transcribe() call.

Empty / silence input
---------------------
Empty bytes return a TranscriptionResult with empty text and confidence 0.0.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jarvis.audio.protocols import (
    CONFIDENCE_CLARIFY,
    CONFIDENCE_PROCEED,
    ConfidenceAction,
    TranscriptionResult,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hotwords per language (T2.3)
# ---------------------------------------------------------------------------

# English hotwords — brand names as-is.
HOTWORDS_EN: str = (
    "YouTube, GitHub, Telegram, Google, Gmail, Wikipedia, Discord, Steam, "
    "Spotify, Reddit, Twitch, Netflix, ChatGPT, Jarvis"
)

# Russian hotwords — Cyrillic spellings so the RU Piper voice reads them
# correctly, plus Latin originals as backup for Whisper decoding.
HOTWORDS_RU: str = (
    "YouTube, Ютуб, GitHub, ГитХаб, Telegram, Телеграм, Google, Гугл, "
    "Gmail, Wikipedia, Википедия, Discord, Дискорд, Steam, Стим, "
    "Spotify, Спотифай, Reddit, Реддит, Twitch, Твич, Netflix, Нетфликс, "
    "ChatGPT, Jarvis, Джарвис"
)

# Ukrainian hotwords — same approach.
HOTWORDS_UK: str = (
    "YouTube, Ютуб, GitHub, ГітХаб, Telegram, Телеграм, Google, Гугл, "
    "Gmail, Wikipedia, Вікіпедія, Discord, Діскорд, Steam, Стім, "
    "Spotify, Спотіфай, Reddit, Реддіт, Twitch, Твіч, Netflix, Нетфлікс, "
    "ChatGPT, Jarvis, Джарвіс"
)

# Combined hotwords for auto-detect mode — covers all three languages.
HOTWORDS_AUTO: str = (
    "YouTube, Ютуб, GitHub, ГитХаб, ГітХаб, Telegram, Телеграм, Google, Гугл, "
    "Gmail, Wikipedia, Википедия, Вікіпедія, Discord, Дискорд, Діскорд, "
    "Steam, Стим, Стім, Spotify, Reddit, Twitch, Netflix, ChatGPT, "
    "Jarvis, Джарвис, Джарвіс"
)

# Map language code to hotword set.
HOTWORDS_BY_LANG: dict[str, str] = {
    "en": HOTWORDS_EN,
    "ru": HOTWORDS_RU,
    "uk": HOTWORDS_UK,
    "auto": HOTWORDS_AUTO,
}


def _normalize_for_echo_check(text: str) -> str:
    return " ".join(text.lower().replace(",", " ").replace(".", " ").split())


class STTLoadError(RuntimeError):  # noqa: N818
    """Raised when the whisper model cannot be loaded."""


def _resolve_model_id(model_size: str, language: str) -> str:
    """Map (model_size, language) to a faster-whisper model identifier.

    English-specific variants exist for tiny/base/small/medium and are
    slightly faster + more accurate for English-only use, so we prefer
    them when language is 'en' and the size has an English variant.
    For multilingual use (language != 'en') we always use the base model."""
    if language == "en" and model_size in ("tiny", "base", "small", "medium"):
        return f"{model_size}.en"
    return model_size


# ---------------------------------------------------------------------------
# Confidence scoring (T2.4)
# ---------------------------------------------------------------------------

def _compute_confidence(
    segments_data: list[dict],
    no_speech_prob_threshold: float = 0.6,
) -> float:
    """Compute an overall 0.0-1.0 confidence from segment-level metrics.

    faster-whisper segments have:
    - avg_logprob: average log probability (typically -1.0 to 0.0, higher = better)
    - no_speech_prob: probability that the segment is not speech (0.0-1.0)

    Strategy:
    1. If ALL segments have no_speech_prob > threshold → confidence 0.0
    2. Otherwise: convert avg_logprob to 0-1 scale, penalize by (1-no_speech_prob)
    3. Average across segments, weighted by segment length (text chars).
    """
    if not segments_data:
        return 0.0

    total_weight = 0.0
    weighted_conf = 0.0

    for seg in segments_data:
        avg_lp = seg.get("avg_logprob", -1.0)
        nsp = seg.get("no_speech_prob", 0.0)
        text_len = len(seg.get("text", "").strip())

        if text_len == 0:
            continue

        # Convert avg_logprob to 0-1 (logprob of 0 = perfect, -1 = poor)
        # Using sigmoid-like mapping: exp(avg_logprob) clamped to [0, 1]
        prob = min(1.0, math.exp(avg_lp))

        # Penalize by speech probability (1 - no_speech_prob)
        speech_factor = max(0.0, 1.0 - nsp) if nsp < no_speech_prob_threshold else 0.0

        seg_confidence = prob * speech_factor
        weight = float(text_len)
        weighted_conf += seg_confidence * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return min(1.0, max(0.0, weighted_conf / total_weight))


def _confidence_action(
    confidence: float,
    proceed_threshold: float = CONFIDENCE_PROCEED,
    clarify_threshold: float = CONFIDENCE_CLARIFY,
) -> ConfidenceAction:
    """Map confidence to action: proceed / clarify / ignore."""
    if confidence >= proceed_threshold:
        return "proceed"
    if confidence >= clarify_threshold:
        return "clarify"
    return "ignore"


# ---------------------------------------------------------------------------
# FasterWhisperSTT
# ---------------------------------------------------------------------------

class FasterWhisperSTT:
    name: str = "stt"

    def __init__(
        self,
        *,
        model_size: str = "base",
        language: str = "en",
        compute_type: str = "int8",
        download_root: Path | None = None,
        device: str = "cpu",
        # None or "" disables the hint.
        hotwords: str | None = None,
        # T2.4 confidence thresholds
        confidence_proceed: float = CONFIDENCE_PROCEED,
        confidence_clarify: float = CONFIDENCE_CLARIFY,
        # T2.4 max retries for "clarify" action
        max_clarify_retries: int = 3,
    ) -> None:
        self.model_size = model_size
        self.language = language
        self.compute_type = compute_type
        self._download_root = download_root
        self._device = device
        self._confidence_proceed = confidence_proceed
        self._confidence_clarify = confidence_clarify
        self.max_clarify_retries = max_clarify_retries
        self._model = None
        self.is_loaded: bool = False

        # Auto-select hotwords by language if not explicitly provided.
        if hotwords is None:
            self.hotwords = HOTWORDS_BY_LANG.get(language, HOTWORDS_AUTO)
        else:
            self.hotwords = hotwords

    # -- Loadable --

    async def load(self) -> None:
        if self.is_loaded:
            return
        # Late import: faster-whisper loads ctranslate2 at import time;
        # keep that off any code path that doesn't actually need STT.
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover - hard dep
            raise STTLoadError("faster_whisper is not installed") from e

        model_id = _resolve_model_id(self.model_size, self.language)
        try:
            # Construction can download large model files on first use;
            # run in a thread so the loop stays responsive throughout.
            kwargs: dict = {
                "device": self._device,
                "compute_type": self.compute_type,
            }
            if self._download_root is not None:
                kwargs["download_root"] = str(self._download_root)
                kwargs["local_files_only"] = True
            self._model = await asyncio.to_thread(
                WhisperModel,
                model_id,
                **kwargs,
            )
        except Exception as e:
            raise STTLoadError(
                f"could not load whisper model {model_id!r}: {e}"
            ) from e
        self.is_loaded = True

    async def unload(self) -> None:
        if not self.is_loaded:
            return
        self._model = None
        self.is_loaded = False

    # -- SpeechToText --

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        """Transcribe audio and return a rich result with confidence."""
        if self._model is None:
            log.warning("transcribe() called before load(); returning empty")
            return TranscriptionResult(text="", confidence=0.0, action="ignore")
        if not audio:
            return TranscriptionResult(text="", confidence=0.0, action="ignore")

        # Convert int16 PCM bytes -> float32 normalized at the stage
        # boundary, per SPEC § Audio Pipeline.
        audio_np = (
            np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        )
        try:
            result = await asyncio.to_thread(self._sync_transcribe, audio_np)
        except Exception:
            log.exception("whisper transcribe raised")
            return TranscriptionResult(text="", confidence=0.0, action="ignore")

        # faster-whisper sometimes emits leading/trailing whitespace;
        # strip so the pipeline's empty-text check works correctly.
        text = result.text.strip()

        # Known Whisper failure mode with a prompt: on near-silence it can
        # "transcribe" the prompt itself. A result that is just the hint
        # list is not something the user said.
        if (
            text
            and self.hotwords
            and _normalize_for_echo_check(text) == _normalize_for_echo_check(self.hotwords)
        ):
            log.info("whisper echoed the hotwords hint; treating as silence")
            return TranscriptionResult(text="", confidence=0.0, action="ignore")

        return TranscriptionResult(
            text=text,
            confidence=result.confidence,
            detected_language=result.detected_language,
            language_probability=result.language_probability,
            action=result.action,
        )

    # -- internal --

    def _sync_transcribe(self, audio: np.ndarray) -> TranscriptionResult:
        """Run faster-whisper inference synchronously. Always called inside
        asyncio.to_thread; never on the loop thread."""
        assert self._model is not None
        # vad_filter=True runs faster-whisper's internal silero-VAD over the
        # input audio to suppress non-speech regions before decoding. Adds
        # ~50 ms of CPU per transcription but dramatically reduces
        # whisper's known hallucination behavior on short / low-signal
        # clips ("Thanks for watching!", "..."). Worth it for the quality
        # win even though the pipeline already runs VAD upstream -- they
        # operate on different signal shapes (live frames vs. captured
        # utterance).
        kwargs: dict = {
            "beam_size": 5,
            "vad_filter": True,
        }

        # T2.1: language="auto" means let Whisper detect.
        if self.language == "auto":
            # Don't pass language= so faster-whisper auto-detects.
            pass
        else:
            kwargs["language"] = self.language

        if self.hotwords:
            kwargs["hotwords"] = self.hotwords

        segments, info = self._model.transcribe(audio, **kwargs)

        # Materialize the segment generator INSIDE this thread; iterating
        # the generator IS the inference work. Returning an unconsumed
        # generator across the to_thread boundary would defer that work
        # back onto the loop thread, defeating the whole point.
        segments_data: list[dict] = []
        text_parts: list[str] = []
        for seg in segments:
            text_parts.append(seg.text)
            segments_data.append({
                "text": seg.text,
                "avg_logprob": seg.avg_logprob,
                "no_speech_prob": seg.no_speech_prob,
            })

        text = "".join(text_parts)

        # T2.4: Compute confidence from segment metrics.
        confidence = _compute_confidence(segments_data)
        action = _confidence_action(
            confidence,
            proceed_threshold=self._confidence_proceed,
            clarify_threshold=self._confidence_clarify,
        )

        # T2.2: Language info from Whisper's auto-detection.
        detected_language: str | None = None
        language_probability: float = 1.0
        if hasattr(info, "language"):
            detected_language = info.language
        if hasattr(info, "language_probability"):
            language_probability = info.language_probability

        return TranscriptionResult(
            text=text,
            confidence=confidence,
            detected_language=detected_language,
            language_probability=language_probability,
            action=action,
        )
