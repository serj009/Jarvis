"""jarvis.dev.smoke_test: the Whisper check (found by pyright, M2-1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.audio.protocols import TranscriptionResult
from jarvis.dev import smoke_test


class _FakeSTT:
    text = ""

    def __init__(self, **kwargs) -> None:
        pass

    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        return TranscriptionResult(text=self.text)


def _ctx():
    stt = SimpleNamespace(model_size="small", language="auto", compute_type="int8")
    return smoke_test.Context(cfg=SimpleNamespace(stt=stt))


def test_silence_passes(monkeypatch):
    """transcribe() returns a TranscriptionResult; the check tested the
    object itself (always truthy) and then took len() of it, so it failed
    on every run, silence or not."""
    monkeypatch.setattr("jarvis.audio.stt.FasterWhisperSTT", _FakeSTT)
    assert smoke_test.check_whisper(_ctx()) == "model=small, language=auto"


def test_text_from_silence_is_a_warning(monkeypatch):
    class Hallucinating(_FakeSTT):
        text = "Thank you."

    monkeypatch.setattr("jarvis.audio.stt.FasterWhisperSTT", Hallucinating)
    with pytest.raises(smoke_test.CheckWarningError, match="10 chars"):
        smoke_test.check_whisper(_ctx())
