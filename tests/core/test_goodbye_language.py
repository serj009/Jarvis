"""jarvis.app.goodbye_language: the shutdown goodbye follows the conversation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.app import goodbye_language


@pytest.mark.parametrize(
    ("spoken", "stt_language", "expected"),
    [
        # Live session: STT in auto mode, the talk was Russian, the goodbye
        # came out in English.
        ("ru", "auto", "ru"),
        ("uk", "auto", "uk"),
        # What Jarvis last spoke beats a fixed STT language.
        ("uk", "ru", "uk"),
        # No usable TTS language -> a fixed STT language.
        (None, "uk", "uk"),
        ("de", "ru", "ru"),
        # Nothing known -> English.
        (None, "auto", "en"),
    ],
)
def test_goodbye_language(spoken, stt_language, expected):
    tts_manager = SimpleNamespace(current_language=spoken) if spoken else None
    stt = SimpleNamespace(language=stt_language)
    assert goodbye_language(tts_manager, stt) == expected
