"""stt.language="auto" picks only among the languages Jarvis speaks (ru/uk/en).

Live regression 2026-11-02: Whisper small heard Ukrainian as Polish
('pl', p=0.54) and asked to repeat, then as Russian (p=0.30).
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from jarvis.audio.stt import (
    DEFAULT_AUTO_LANGUAGES,
    FasterWhisperSTT,
    _pick_allowed_language,
)

_UK_AS_PL = [("pl", 0.54), ("uk", 0.30), ("ru", 0.10), ("en", 0.02), ("cs", 0.04)]


class _Transcriber:
    """Whisper stand-in: records transcribe kwargs."""

    def __init__(self, all_probs=None, *, vad_raises: bool = False, detect_raises=None):
        self.all_probs = all_probs or _UK_AS_PL
        self.vad_raises = vad_raises
        self.detect_raises = detect_raises
        self.detect_calls: list[dict] = []
        self.transcribe_kwargs: dict | None = None

    @property
    def sent(self) -> dict:
        """The kwargs transcribe() was called with (it must have been)."""
        assert self.transcribe_kwargs is not None, "transcribe() was not called"
        return self.transcribe_kwargs

    def transcribe(self, audio, **kwargs):
        self.transcribe_kwargs = kwargs
        lang = kwargs.get("language") or self.all_probs[0][0]
        seg = types.SimpleNamespace(text="привіт", avg_logprob=-0.2, no_speech_prob=0.1)
        info = types.SimpleNamespace(
            language=lang, language_probability=1.0 if "language" in kwargs else 0.54,
        )
        return iter([seg]), info


class _FakeModel(_Transcriber):
    """...with a scripted detect_language."""

    def detect_language(self, audio, **kwargs):
        self.detect_calls.append(kwargs)
        if self.detect_raises is not None:
            raise self.detect_raises
        if self.vad_raises and kwargs.get("vad_filter"):
            raise ValueError("need at least one array to concatenate")
        top, top_p = self.all_probs[0]
        return top, top_p, list(self.all_probs)


class _NoDetectModel(_Transcriber):
    """No detect_language: older faster-whisper / test doubles."""


def _stt(model, **kwargs) -> FasterWhisperSTT:
    stt = FasterWhisperSTT(**{"language": "auto", **kwargs})
    stt._model = model
    stt.is_loaded = True
    return stt


_AUDIO = np.zeros(16000, dtype=np.float32)


# -- pure helper ------------------------------------------------------------


def test_pick_skips_languages_jarvis_does_not_speak():
    picked = _pick_allowed_language(_UK_AS_PL, DEFAULT_AUTO_LANGUAGES)
    assert picked is not None
    lang, prob = picked
    assert lang == "uk"
    assert prob == pytest.approx(0.30 / 0.42)


def test_pick_returns_none_without_allowed_languages():
    assert _pick_allowed_language([("pl", 0.9), ("cs", 0.1)], DEFAULT_AUTO_LANGUAGES) is None
    assert _pick_allowed_language([], DEFAULT_AUTO_LANGUAGES) is None


def test_default_languages_are_the_three_voices():
    assert set(DEFAULT_AUTO_LANGUAGES) == {"ru", "uk", "en"}


# -- _sync_transcribe -------------------------------------------------------


def test_auto_mode_forces_best_allowed_language():
    model = _FakeModel()
    result = _stt(model)._sync_transcribe(_AUDIO)
    assert model.sent["language"] == "uk"
    assert result.detected_language == "uk"
    assert result.language_probability == pytest.approx(0.30 / 0.42)


def test_allowed_top_language_is_kept():
    model = _FakeModel([("ru", 0.95), ("uk", 0.04), ("en", 0.01)])
    result = _stt(model)._sync_transcribe(_AUDIO)
    assert model.sent["language"] == "ru"
    assert result.detected_language == "ru"


def test_vad_failure_retries_detection_without_vad():
    model = _FakeModel(vad_raises=True)
    result = _stt(model)._sync_transcribe(_AUDIO)
    assert [c.get("vad_filter") for c in model.detect_calls] == [True, None]
    assert result.detected_language == "uk"


def test_detection_error_falls_back_to_free_detection():
    model = _FakeModel(detect_raises=RuntimeError("boom"))
    result = _stt(model)._sync_transcribe(_AUDIO)
    assert "language" not in model.sent
    assert result.detected_language == "pl"  # old behaviour, no crash


def test_model_without_detect_language_keeps_old_behaviour():
    model = _NoDetectModel()
    _stt(model)._sync_transcribe(_AUDIO)
    assert "language" not in model.sent


def test_empty_auto_languages_disables_the_restriction():
    model = _FakeModel()
    _stt(model, auto_languages=())._sync_transcribe(_AUDIO)
    assert model.detect_calls == []
    assert "language" not in model.sent


def test_fixed_language_skips_detection():
    model = _FakeModel()
    _stt(model, language="ru")._sync_transcribe(_AUDIO)
    assert model.detect_calls == []
    assert model.sent["language"] == "ru"
