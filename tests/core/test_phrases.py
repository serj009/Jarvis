"""Spoken phrases follow the language of the request (ru / uk / en)."""

from __future__ import annotations

import asyncio
from typing import cast

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
from jarvis.tools.registry import ToolRegistry, ToolResult


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
        # No і/ї/є/ґ: told apart by the command word.
        ("наступний трек", "uk"),
        ("закрий блокнот", "uk"),
        ("постав таймер на п'ять хвилин", "uk"),
        ("яка погода", "uk"),
        ("почни диктовку", "uk"),
        ("следующий трек", "ru"),
        ("закрой блокнот", "ru"),
        ("поставь таймер на пять минут", "ru"),
        ("какая погода", "ru"),
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
        # _Tool stands in for the registry's execute() / get() only.
        registry = cast(ToolRegistry, _Tool(result))
        return [c async for c in execute_intent(intent, registry)]

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


# ── B-P2-9: timers and reminders speak the language they were set in ──────


async def test_timer_set_in_russian_is_announced_in_russian(tmp_path):
    """Live session: a timer set in Russian said "Timer 'Одна минута' is
    done, sir!". The countdown task keeps the setting request's context,
    so the announcement follows that request's language."""
    from jarvis.core.phrases import timer_done_phrase
    from jarvis.tools.local.timers import TimerStore

    spoken: list[str] = []
    store = TimerStore(
        on_timer_done=lambda name: spoken.append(timer_done_phrase(name)),
        persist_path=tmp_path / "timers.json",
    )
    token = current_user_transcription.set("Поставь таймер на одну минуту")
    try:
        assert store.set_timer(0.05, "Одна минута")["success"]
    finally:
        # The turn is over long before the timer runs out.
        current_user_transcription.reset(token)

    await asyncio.sleep(0.2)
    assert spoken == ["Таймер «Одна минута» истёк, сэр!"]


@pytest.mark.parametrize(
    ("request_text", "name", "expected"),
    [
        ("set a timer for tea", "tea", "Timer 'tea' is done, sir!"),
        ("Постав таймер на чай і печиво", "чай", "Таймер «чай» спрацював, сер!"),
        # The model may name the timer in English; the request decides.
        ("Поставь таймер на пасту", "pasta", "Таймер «pasta» истёк, сэр!"),
        # No request text (timer restored outside a turn): the name decides.
        (None, "Одна минута", "Таймер «Одна минута» истёк, сэр!"),
        (None, "tea", "Timer 'tea' is done, sir!"),
        # Unnamed timer: "timer_1" is not read out.
        ("Поставь таймер на минуту", "timer_1", "Таймер истёк, сэр."),
        (None, "timer_3", "Timer is up, sir."),
    ],
)
def test_timer_done_phrase(utterance, request_text, name, expected):
    from jarvis.core.phrases import timer_done_phrase

    utterance(request_text)
    assert timer_done_phrase(name) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("купить молоко", "Напоминаю, сэр: купить молоко"),
        ("подзвонити мамі", "Нагадую, сер: подзвонити мамі"),
        ("call mom", "Reminder, sir: call mom"),
    ],
)
def test_reminder_phrase_follows_reminder_text(utterance, text, expected):
    """A reminder fires outside any request: its own text decides."""
    from jarvis.core.phrases import reminder_phrase

    utterance(None)
    assert reminder_phrase(text) == expected


def test_app_announces_timers_and_reminders_through_phrases():
    """app.py used hard-coded English f-strings for both announcements."""
    from pathlib import Path

    import jarvis.app

    source = Path(jarvis.app.__file__).read_text(encoding="utf-8")
    assert "is done, sir!" not in source
    assert "Reminder, sir:" not in source
    assert "timer_done_phrase(name)" in source
    assert "reminder_phrase(r.text)" in source


# ── B-P2-13: no Latin "sir" in Russian / Ukrainian phrases ────────────────


@pytest.mark.parametrize("key", sorted(PHRASES))
def test_ru_uk_phrases_have_no_latin_sir(key):
    """The ru / uk Piper voices misread a Latin "sir"; it is "сэр" / "сер"."""
    import re

    for lang in ("ru", "uk"):
        text = PHRASES[key].get(lang, "")
        assert not re.search(r"\bsir\b", text, re.IGNORECASE), (key, lang, text)


def test_agent_progress_lines_have_no_latin_sir():
    import re
    from pathlib import Path

    import jarvis.agents.autonomous as autonomous

    source = Path(autonomous.__file__).read_text(encoding="utf-8")
    cyrillic_with_sir = re.findall(r'"[^"\n]*[а-яё][^"\n]*\bsir\b[^"\n]*"', source)
    assert cyrillic_with_sir == []


# Keys built at runtime: say(f"media_{action}"), say(f"window_{action}"),
# say(f"desktop_{action}"), say(f"module_down_{module}") and the pipeline's
# thinking_* list.
_DYNAMIC_PREFIXES = ("media_", "window_", "desktop_", "module_down_")
# Kept although nothing says them yet, with the reason.
_NOT_SAID_YET = {
    "error_ollama": "no error type carries it; Ollama failures are spoken as error_generic",
    "error_llm_timeout": "listed with the timeout phrases the pipeline tests check",
    "thinking_long_1": "listed with the thinking phrases the pipeline tests check",
}


def test_every_phrase_is_said_somewhere():
    """14 keys (volume_set, confirm_ask, coding_*, ...) were never said, and
    a home check expected two of them."""
    from pathlib import Path

    import jarvis

    root = Path(jarvis.__file__).parent
    source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in root.rglob("*.py")
        if p != root / "core" / "phrases.py"
    )
    own = (root / "core" / "phrases.py").read_text(encoding="utf-8")
    helpers = own[own.index("def say("):]
    unused = [
        key for key in PHRASES
        if f'"{key}"' not in source and f'"{key}"' not in helpers
        and not key.startswith(_DYNAMIC_PREFIXES) and key not in _NOT_SAID_YET
    ]
    assert unused == []
