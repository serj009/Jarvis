"""Free-form "speak <language>" commands lock the language (live 2026-11-02).

A 43-char "говори українською ..." variant missed the exact patterns, went to
the LLM, and the language was never locked. Matching here goes through the
router's own normalisation (_normalize) and re.match, exactly like the
pattern layer.
"""

from __future__ import annotations

import re

import pytest

from jarvis.audio.pipeline import _language_switch_reason
from jarvis.llm.intent_router import _normalize
from jarvis.tools.local.switch_language import SwitchLanguageTool


def _route(utterance: str) -> str | None:
    normalized = _normalize(utterance)
    for vp in SwitchLanguageTool.voice_patterns:
        m = re.match(vp.regex, normalized)
        if m:
            return vp.args(m)["language"]
    return None


@pytest.mark.parametrize(
    ("utterance", "lang"),
    [
        ("Говори зі мною українською мовою.", "uk"),
        ("Джарвіс, говори українською, будь ласка", "uk"),
        ("Розмовляй зі мною українською", "uk"),
        ("Відповідай українською мовою", "uk"),
        ("Давай говорити українською", "uk"),
        ("Перейди на українську мову", "uk"),
        ("Говори по-украински", "uk"),
        ("Переключись на украинский язык", "uk"),
        ("Speak Ukrainian, please", "uk"),
        ("Talk to me in Ukrainian", "uk"),
        ("Отвечай на русском", "ru"),
        ("Говори російською", "ru"),
        ("Speak Russian", "ru"),
        ("Говори англійською", "en"),
        ("Answer in English", "en"),
    ],
)
def test_free_form_commands_lock_the_language(utterance, lang):
    assert _route(utterance) == lang


@pytest.mark.parametrize(
    "utterance",
    [
        # NB: "Говори українською, яке в мене місто?" still matches the OLD
        # prefix-only pattern (^говори\s+українською) -- existing behaviour,
        # left as is.
        "Переведи на українську мову",            # translate request
        "Как будет на украинском привет?",
        "Яке в мене місто?",
        "Говори громче",
        "Скажи українською слово кіт",
    ],
)
def test_other_phrases_are_not_language_commands(utterance):
    assert _route(utterance) is None


# -- voice switch floor -----------------------------------------------------


def _reason(text: str, lang: str = "uk", prob: float = 0.60) -> str | None:
    return _language_switch_reason(
        detected_language=lang, language_probability=prob,
        text_len=len(text), min_probability=0.70, min_chars=8, text=text,
    )


def test_ukrainian_letters_allow_switch_below_probability_floor():
    # Live 2026-11-02: uk=0.33 vs ru=0.21 -> p=0.60; the voice stayed English.
    # "місто" carries the Ukrainian-only letter "і".
    assert _reason("Яке в мене місто?") is None


def test_ukrainian_without_ukrainian_only_letters_is_not_covered():
    # Known limit: "и"/"й" exist in Russian too, so a Ukrainian phrase
    # without і/ї/є/ґ still needs Whisper's probability to clear the floor.
    assert _reason("Який у мене браузер?") == "low-probability"
    assert _reason("Який у мене браузер?", prob=0.75) is None


def test_low_probability_without_ukrainian_letters_still_blocks():
    assert _reason("Какой у меня браузер?") == "low-probability"
    assert _reason("Какой у меня браузер?", lang="ru") == "low-probability"


def test_short_text_still_blocks():
    assert _reason("Їж") == "short-text"


def test_old_callers_without_text_keep_old_behaviour():
    assert _language_switch_reason(
        detected_language="uk", language_probability=0.60,
        text_len=20, min_probability=0.70, min_chars=8,
    ) == "low-probability"
