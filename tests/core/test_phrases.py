"""Spoken phrases follow the language of the request (ru / uk / en)."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core import phrases
from jarvis.core.phrases import (
    PHRASES,
    detect_language,
    opening_phrase,
    reply_language,
    say,
    spoken_name,
)
from jarvis.core.request_context import current_user_transcription
from jarvis.llm.intent_router import ToolIntent, execute_intent
from jarvis.tools.registry import ToolResult


@pytest.fixture
def utterance():
    """Set the current request text for the duration of a test."""
    tokens = []

    def _set(text: str | None) -> None:
        tokens.append(current_user_transcription.set(text))

    yield _set
    for token in reversed(tokens):
        current_user_transcription.reset(token)


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("Открой ютуб", "ru"),
        ("Открой YouTube Music", "ru"),
        ("Jarvis, открой гитхаб", "ru"),
        ("Загугли как пройти Stalker 2", "ru"),
        ("Привет, как дела?", "ru"),
        ("Відкрий стім", "uk"),
        ("Привіт, як справи?", "uk"),
        ("Що це таке", "uk"),
        ("Open YouTube", "en"),
        ("What time is it?", "en"),
    ],
)
def test_detect_language(text, lang):
    assert detect_language(text) == lang


@pytest.mark.parametrize("text", [None, "", "123", "?!"])
def test_detect_language_without_letters(text):
    assert detect_language(text) is None


def test_no_request_keeps_english(utterance):
    utterance(None)
    assert reply_language() == "en"
    assert opening_phrase("YouTube") == "Opening YouTube, sir."
    assert say("done") == "Done, sir."


def test_every_phrase_has_all_languages():
    for key, variants in PHRASES.items():
        assert set(variants) == set(phrases.LANGS), key


def test_opening_in_request_language(utterance):
    utterance("Открой ютуб")
    assert opening_phrase("YouTube") == "Открываю Ютуб, сэр."
    utterance("Відкрий ютуб")
    assert opening_phrase("YouTube") == "Відкриваю Ютуб, сер."
    utterance("Open YouTube")
    assert opening_phrase("YouTube") == "Opening YouTube, sir."


def test_spoken_name_uses_cyrillic_and_case():
    assert spoken_name("Steam", "ru") == "Стим"
    assert spoken_name("Steam", "uk") == "Стім"
    assert spoken_name("Wikipedia", "ru") == "Википедию"
    assert spoken_name("Google  Chrome", "ru") == "Гугл Хром"
    assert spoken_name("Steam", "en") == "Steam"


def test_unknown_latin_name_is_left_out_in_ru_uk():
    assert spoken_name("Some Tool", "ru") is None
    assert opening_phrase("Some Tool", "ru") == "Открываю, сэр."
    assert opening_phrase("Some Tool", "uk") == "Відкриваю, сер."
    # A Cyrillic name is said as is.
    assert opening_phrase("Блокнот", "ru") == "Открываю Блокнот, сэр."


class _Tool:
    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def execute(self, name, args):  # registry.execute signature
        return self._result

    def get(self, name):
        return None


def _speak(result: ToolResult) -> list[str]:
    async def run():
        intent = ToolIntent("thing", {})
        return [c async for c in execute_intent(intent, _Tool(result))]

    return asyncio.run(run())


def test_failure_is_spoken_in_russian_without_raw_error(utterance):
    utterance("Открой что-нибудь")
    chunks = _speak(ToolResult(success=False, error="could not launch 'x': WinError 2"))
    assert chunks == ["Боюсь, что-то пошло не так. "]


def test_failure_in_english_keeps_the_error(utterance):
    utterance("Open something")
    chunks = _speak(ToolResult(success=False, error="I don't see it running, sir."))
    assert chunks == ["I don't see it running, sir. "]


def test_done_in_ukrainian(utterance):
    utterance("Зроби щось")
    assert _speak(ToolResult(success=True, output=None)) == ["Зроблено, сер."]
