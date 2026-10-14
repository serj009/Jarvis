"""Tests for jarvis.tools.local.get_time.GetTimeTool."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from jarvis.tools.local.get_time import GetTimeTool
from jarvis.tools.registry import EmptyArgs


# ── execution ─────────────────────────────────────────────────────


async def test_execute_returns_time():
    """Output must contain a colon (HH:MM) — basic time format check."""
    result = await GetTimeTool().execute(EmptyArgs())
    assert result.success
    assert ":" in (result.output or "")


async def test_execute_english_format():
    """Default (no request context) → English output with AM/PM."""
    result = await GetTimeTool().execute(EmptyArgs())
    out = result.output or ""
    assert "sir" in out.lower()
    # Should contain AM or PM
    assert "AM" in out or "PM" in out


async def test_execute_russian_format():
    """When reply_language() returns 'ru', output is in Russian."""
    with patch(
        "jarvis.tools.local.get_time.reply_language", return_value="ru"
    ):
        result = await GetTimeTool().execute(EmptyArgs())
    out = result.output or ""
    assert "Сейчас" in out
    assert ":" in out


async def test_execute_ukrainian_format():
    """When reply_language() returns 'uk', output is in Ukrainian."""
    with patch(
        "jarvis.tools.local.get_time.reply_language", return_value="uk"
    ):
        result = await GetTimeTool().execute(EmptyArgs())
    out = result.output or ""
    assert "Зараз" in out
    assert ":" in out


# ── voice patterns ────────────────────────────────────────────────


def test_voice_patterns_compile():
    """Every regex in voice_patterns must compile without error."""
    for vp in GetTimeTool.voice_patterns:
        re.compile(vp.regex, re.IGNORECASE)


_EN_PHRASES = [
    "what time is it",
    "what time is it?",
    "time",
    "time?",
    "what's the date",
    "what is the date",
    "date",
    "what day is today",
    "what day is it today",
    "today's date",
]


@pytest.mark.parametrize("phrase", _EN_PHRASES)
def test_voice_patterns_match_en(phrase: str):
    """English phrases must match at least one voice pattern."""
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


_RU_PHRASES = [
    "сколько времени",
    "сколько времени?",
    "сколько сейчас времени",
    "который час",
    "который час?",
    "какое сегодня число",
    "какой сегодня день",
]


@pytest.mark.parametrize("phrase", _RU_PHRASES)
def test_voice_patterns_match_ru(phrase: str):
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


_UK_PHRASES = [
    "котра година",
    "котра година?",
    "скільки часу",
    "скільки зараз часу",
    "яке сьогодні число",
    "який сьогодні день",
]


@pytest.mark.parametrize("phrase", _UK_PHRASES)
def test_voice_patterns_match_uk(phrase: str):
    assert _matches_any(phrase), f"No pattern matched: {phrase!r}"


# ── helpers ───────────────────────────────────────────────────────


def _matches_any(text: str) -> bool:
    """Return True if *text* matches any voice pattern on GetTimeTool."""
    for vp in GetTimeTool.voice_patterns:
        if re.match(vp.regex, text, re.IGNORECASE):
            return True
    return False
