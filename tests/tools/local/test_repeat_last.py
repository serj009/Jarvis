"""Tests for jarvis.tools.local.repeat_last.RepeatLastTool."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.core.last_spoken import clear_last_spoken, get_last_spoken, set_last_spoken
from jarvis.tools.local.repeat_last import RepeatLastTool
from jarvis.tools.registry import EmptyArgs

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_last_spoken():
    """Reset the last_spoken store before and after every test."""
    clear_last_spoken()
    yield
    clear_last_spoken()


# ── last_spoken store unit tests ──────────────────────────────────────


def test_store_initially_none():
    """Before anything is spoken, get_last_spoken returns None."""
    assert get_last_spoken() is None


def test_store_set_and_get():
    """set_last_spoken / get_last_spoken round-trip."""
    set_last_spoken("Hello, sir.")
    assert get_last_spoken() == "Hello, sir."


def test_store_overwrites():
    """A second set_last_spoken overwrites the first."""
    set_last_spoken("first")
    set_last_spoken("second")
    assert get_last_spoken() == "second"


def test_store_clear():
    """clear_last_spoken resets to None."""
    set_last_spoken("something")
    clear_last_spoken()
    assert get_last_spoken() is None


# ── tool execution ────────────────────────────────────────────────────


async def test_execute_returns_last_spoken():
    """When a text was spoken, execute returns it verbatim."""
    set_last_spoken("The weather in London is 14 degrees, sir.")
    result = await RepeatLastTool().execute(EmptyArgs())
    assert result.success
    assert result.output == "The weather in London is 14 degrees, sir."


async def test_execute_nothing_spoken_en():
    """With no prior speech and EN language, returns English fallback."""
    with patch("jarvis.core.phrases.reply_language", return_value="en"):
        result = await RepeatLastTool().execute(EmptyArgs())
    assert result.success
    assert "haven't said anything" in (result.output or "").lower()


async def test_execute_nothing_spoken_ru():
    """With no prior speech and RU language, returns Russian fallback."""
    with patch("jarvis.core.phrases.reply_language", return_value="ru"):
        result = await RepeatLastTool().execute(EmptyArgs())
    assert result.success
    assert "ничего не говорил" in (result.output or "")


async def test_execute_nothing_spoken_uk():
    """With no prior speech and UK language, returns Ukrainian fallback."""
    with patch("jarvis.core.phrases.reply_language", return_value="uk"):
        result = await RepeatLastTool().execute(EmptyArgs())
    assert result.success
    assert "нічого не казав" in (result.output or "")


async def test_execute_preserves_multiline():
    """Multi-line response text is preserved verbatim."""
    text = "Line one.\nLine two.\nLine three."
    set_last_spoken(text)
    result = await RepeatLastTool().execute(EmptyArgs())
    assert result.output == text


async def test_execute_empty_string_treated_as_nothing():
    """An empty string is falsy, so the tool returns the 'nothing' message."""
    set_last_spoken("")
    with patch("jarvis.core.phrases.reply_language", return_value="en"):
        result = await RepeatLastTool().execute(EmptyArgs())
    assert result.success
    assert "haven't said anything" in (result.output or "").lower()


# ── voice patterns ────────────────────────────────────────────────────


def test_voice_patterns_compile():
    """Every regex in voice_patterns must compile without error."""
    for vp in RepeatLastTool.voice_patterns:
        re.compile(vp.regex, re.IGNORECASE)


_EN_PHRASES = [
    "repeat",
    "repeat that",
    "what did you say",
    "what did you say?",
    "say again",
    "say that again",
    "come again",
    "come again?",
]


@pytest.mark.parametrize("phrase", _EN_PHRASES)
def test_voice_patterns_match_en(phrase: str):
    """English phrases must match at least one voice pattern."""
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


_RU_PHRASES = [
    "повтори",
    "что сказал",
    "что сказал?",
    "что ты сказал",
    "что ты сказал?",
    "скажи ещё раз",
]


@pytest.mark.parametrize("phrase", _RU_PHRASES)
def test_voice_patterns_match_ru(phrase: str):
    """Russian phrases must match at least one voice pattern."""
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


_UK_PHRASES = [
    "повтори",
    "що сказав",
    "що сказав?",
    "що ти сказав",
    "що ти сказав?",
    "скажи ще раз",
]


@pytest.mark.parametrize("phrase", _UK_PHRASES)
def test_voice_patterns_match_uk(phrase: str):
    """Ukrainian phrases must match at least one voice pattern."""
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


_NON_MATCHING = [
    "repeat after me hello",
    "what is the time",
    "say something",
    "play music",
    "open spotify",
]


@pytest.mark.parametrize("phrase", _NON_MATCHING)
def test_voice_patterns_no_false_positives(phrase: str):
    """Unrelated phrases must NOT match any voice pattern."""
    assert not _matches_any(phrase), f"Unexpected match: {phrase!r}"


# ── class contract ────────────────────────────────────────────────────


def test_name_is_repeat_last():
    assert RepeatLastTool.name == "repeat_last"


def test_requires_no_confirmation():
    assert RepeatLastTool.requires_confirmation is False


def test_risk_level_is_read():
    from jarvis.tools.risk import RiskLevel
    assert RepeatLastTool.risk_level is RiskLevel.READ


def test_args_schema_is_empty():
    assert RepeatLastTool.args_schema is EmptyArgs


def test_all_patterns_priority_100():
    """All voice patterns should be priority 100 (highest, snap command)."""
    for vp in RepeatLastTool.voice_patterns:
        assert vp.priority == 100, f"Pattern {vp.regex!r} has priority {vp.priority}"


# ── ast.parse (syntax validation) ─────────────────────────────────────


def test_module_parses():
    """repeat_last.py must be valid Python (ast.parse)."""
    src = Path(__file__).resolve().parents[3] / "jarvis" / "tools" / "local" / "repeat_last.py"
    ast.parse(src.read_text(encoding="utf-8"), filename=str(src))


def test_store_module_parses():
    """last_spoken.py must be valid Python (ast.parse)."""
    src = Path(__file__).resolve().parents[3] / "jarvis" / "core" / "last_spoken.py"
    ast.parse(src.read_text(encoding="utf-8"), filename=str(src))


# ── helpers ───────────────────────────────────────────────────────────


def _matches_any(text: str) -> bool:
    """Return True if *text* matches any voice pattern on RepeatLastTool."""
    for vp in RepeatLastTool.voice_patterns:
        if re.match(vp.regex, text, re.IGNORECASE):
            return True
    return False
