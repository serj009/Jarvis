"""Phase-2 STT tests (T2.1–T2.4).

These tests cover T2.1-T2.4 without duplicating the Phase 1 test suite
in test_stt.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("faster_whisper")

from jarvis.audio.protocols import TranscriptionResult  # noqa: E402
from jarvis.audio.stt import (  # noqa: E402
    HOTWORDS_AUTO,
    HOTWORDS_BY_LANG,
    HOTWORDS_EN,
    HOTWORDS_RU,
    HOTWORDS_UK,
    FasterWhisperSTT,
    _compute_confidence,
    _confidence_action,
    _resolve_model_id,
)


# ---------------------------------------------------------------------------
# TranscriptionResult
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_str_returns_text(self) -> None:
        r = TranscriptionResult(text="hello world")
        assert str(r) == "hello world"

    def test_is_empty_on_blank(self) -> None:
        assert TranscriptionResult(text="").is_empty
        assert TranscriptionResult(text="   ").is_empty
        assert not TranscriptionResult(text="hi").is_empty

    def test_default_confidence_is_1(self) -> None:
        r = TranscriptionResult(text="hi")
        assert r.confidence == 1.0
        assert r.action == "proceed"

    def test_frozen(self) -> None:
        r = TranscriptionResult(text="hi")
        with pytest.raises(AttributeError):
            r.text = "bye"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Confidence scoring (T2.4)
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_empty_segments_returns_zero(self) -> None:
        assert _compute_confidence([]) == 0.0

    def test_high_confidence_segments(self) -> None:
        segs = [{"text": "hello world", "avg_logprob": -0.2, "no_speech_prob": 0.01}]
        conf = _compute_confidence(segs)
        assert conf > 0.7

    def test_low_confidence_segments(self) -> None:
        segs = [{"text": "...", "avg_logprob": -2.0, "no_speech_prob": 0.8}]
        conf = _compute_confidence(segs)
        assert conf < 0.3

    def test_no_speech_high_prob_returns_zero(self) -> None:
        segs = [{"text": "Thanks for watching", "avg_logprob": -0.5, "no_speech_prob": 0.95}]
        conf = _compute_confidence(segs)
        assert conf == 0.0

    def test_weighted_by_text_length(self) -> None:
        segs = [
            {"text": "a", "avg_logprob": -0.1, "no_speech_prob": 0.01},       # short, high
            {"text": "long sentence here", "avg_logprob": -1.5, "no_speech_prob": 0.1},  # long, low
        ]
        conf = _compute_confidence(segs)
        # Should be dragged down by the longer, lower-confidence segment
        assert conf < 0.7

    def test_non_english_speech_reaches_proceed(self) -> None:
        """Regression: correctly transcribed RU/UK speech must clear the
        0.70 "proceed" threshold.

        faster-whisper's avg_logprob runs systematically lower for
        non-English speech and int8/CPU inference. A typical, correctly
        recognised Russian command scores around avg_logprob -0.5; the
        confidence mapping must lift that above 0.70 so it is acted on
        rather than routed to "clarify" ("I didn't understand"). This pins
        the exp(avg_logprob / 2) calibration against a regression to the
        English-only exp(avg_logprob).
        """
        segs = [{"text": "открой браузер", "avg_logprob": -0.5, "no_speech_prob": 0.05}]
        conf = _compute_confidence(segs)
        assert conf >= 0.70
        assert _confidence_action(conf) == "proceed"


class TestConfidenceAction:
    def test_proceed_above_threshold(self) -> None:
        assert _confidence_action(0.85) == "proceed"
        assert _confidence_action(0.70) == "proceed"

    def test_clarify_in_middle(self) -> None:
        assert _confidence_action(0.50) == "clarify"
        assert _confidence_action(0.30) == "clarify"

    def test_ignore_below_threshold(self) -> None:
        assert _confidence_action(0.10) == "ignore"
        assert _confidence_action(0.0) == "ignore"

    def test_custom_thresholds(self) -> None:
        assert _confidence_action(0.60, proceed_threshold=0.5) == "proceed"
        assert _confidence_action(0.20, clarify_threshold=0.25) == "ignore"


# ---------------------------------------------------------------------------
# Hotwords per language (T2.3)
# ---------------------------------------------------------------------------


class TestHotwords:
    def test_language_specific_hotwords_exist(self) -> None:
        assert "en" in HOTWORDS_BY_LANG
        assert "ru" in HOTWORDS_BY_LANG
        assert "uk" in HOTWORDS_BY_LANG
        assert "auto" in HOTWORDS_BY_LANG

    def test_ru_hotwords_have_cyrillic(self) -> None:
        assert "Ютуб" in HOTWORDS_RU
        assert "Джарвис" in HOTWORDS_RU
        assert "Телеграм" in HOTWORDS_RU

    def test_uk_hotwords_have_ukrainian(self) -> None:
        assert "Ютуб" in HOTWORDS_UK
        assert "Джарвіс" in HOTWORDS_UK  # Ukrainian spelling
        assert "Вікіпедія" in HOTWORDS_UK

    def test_auto_hotwords_cover_all_languages(self) -> None:
        assert "Джарвис" in HOTWORDS_AUTO  # Russian
        assert "Джарвіс" in HOTWORDS_AUTO  # Ukrainian
        assert "Jarvis" in HOTWORDS_AUTO   # English

    def test_auto_select_by_language(self) -> None:
        stt = FasterWhisperSTT(language="ru")
        assert "Джарвис" in stt.hotwords

        stt = FasterWhisperSTT(language="uk")
        assert "Джарвіс" in stt.hotwords

        stt = FasterWhisperSTT(language="auto")
        assert "Джарвис" in stt.hotwords
        assert "Джарвіс" in stt.hotwords

    def test_explicit_hotwords_override(self) -> None:
        stt = FasterWhisperSTT(language="ru", hotwords="Custom, Words")
        assert stt.hotwords == "Custom, Words"

    def test_empty_hotwords_disable(self) -> None:
        stt = FasterWhisperSTT(language="ru", hotwords="")
        assert stt.hotwords == ""


# ---------------------------------------------------------------------------
# Multilingual model resolution (T2.1)
# ---------------------------------------------------------------------------


class TestMultilingualModelResolution:
    def test_auto_language_uses_base_model(self) -> None:
        # "auto" is not "en", so no .en suffix
        assert _resolve_model_id("small", "auto") == "small"
        assert _resolve_model_id("base", "auto") == "base"

    def test_russian_uses_base_model(self) -> None:
        assert _resolve_model_id("small", "ru") == "small"

    def test_ukrainian_uses_base_model(self) -> None:
        assert _resolve_model_id("small", "uk") == "small"

    def test_english_uses_en_variant(self) -> None:
        assert _resolve_model_id("small", "en") == "small.en"


# ---------------------------------------------------------------------------
# STT constructor defaults (T2.1 + T2.4)
# ---------------------------------------------------------------------------


class TestSTTConstructor:
    def test_default_confidence_thresholds(self) -> None:
        stt = FasterWhisperSTT()
        assert stt._confidence_proceed == 0.70
        assert stt._confidence_clarify == 0.30
        assert stt.max_clarify_retries == 3

    def test_custom_confidence_thresholds(self) -> None:
        stt = FasterWhisperSTT(
            confidence_proceed=0.80,
            confidence_clarify=0.40,
            max_clarify_retries=5,
        )
        assert stt._confidence_proceed == 0.80
        assert stt._confidence_clarify == 0.40
        assert stt.max_clarify_retries == 5
