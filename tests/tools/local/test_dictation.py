"""Tests for jarvis.tools.local.dictation.

Covers protocol conformance, voice patterns (EN/RU/UK), start/stop
lifecycle, special-command expansion, the DICTATION_STOP_PATTERN guard,
and ast.parse.  All paths are absolute.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from jarvis.tools.local.dictation import (
    DICTATION_STOP_PATTERN,
    DictationArgs,
    DictationTool,
    _set_dictation,
    is_dictation_active,
    process_dictation_text,
)
from jarvis.tools.registry import ToolResult, VoicePattern


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_dictation():
    """Ensure dictation is off before and after every test."""
    _set_dictation(False)
    yield
    _set_dictation(False)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_has_required_attributes(self):
        tool = DictationTool()
        assert tool.name == "dictation"
        assert isinstance(tool.description, str) and tool.description
        assert tool.args_schema is DictationArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(DictationTool, "voice_patterns")
        assert isinstance(DictationTool.voice_patterns, tuple)
        assert all(
            isinstance(p, VoicePattern)
            for p in DictationTool.voice_patterns
        )
        # At least EN start + stop, RU start + stop, UK start + stop = 6
        assert len(DictationTool.voice_patterns) >= 6


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------


class TestVoicePatternsEN:
    @pytest.mark.parametrize("utterance", [
        "start dictation",
        "begin dictation",
        "dictate",
    ])
    def test_start_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No EN start pattern matched: {utterance!r}"
        # Verify the matched pattern produces action=start
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "start"
                break

    @pytest.mark.parametrize("utterance", [
        "stop dictation",
        "end dictation",
        "stop dictating",
    ])
    def test_stop_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No EN stop pattern matched: {utterance!r}"
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "stop"
                break


class TestVoicePatternsRU:
    @pytest.mark.parametrize("utterance", [
        "начни диктовку",
        "начать диктовку",
    ])
    def test_start_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No RU start pattern matched: {utterance!r}"
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "start"
                break

    @pytest.mark.parametrize("utterance", [
        "закончи диктовку",
        "стоп диктовку",
        "останови диктовку",
        "прекрати диктовку",
        "закончи диктовка",
        "стоп диктовка",
    ])
    def test_stop_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No RU stop pattern matched: {utterance!r}"
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "stop"
                break


class TestVoicePatternsUK:
    @pytest.mark.parametrize("utterance", [
        "почни диктовку",
        "розпочни диктовку",
    ])
    def test_start_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No UK start pattern matched: {utterance!r}"
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "start"
                break

    @pytest.mark.parametrize("utterance", [
        "заверши диктовку",
        "зупини диктовку",
        "припини диктовку",
        "стоп диктовка",
    ])
    def test_stop_patterns(self, utterance):
        matched = any(
            re.match(vp.regex, utterance) for vp in DictationTool.voice_patterns
        )
        assert matched, f"No UK stop pattern matched: {utterance!r}"
        for vp in DictationTool.voice_patterns:
            m = re.match(vp.regex, utterance)
            if m:
                assert vp.args(m)["action"] == "stop"
                break


# ---------------------------------------------------------------------------
# Start / stop lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_then_stop(self):
        tool = DictationTool()
        assert not is_dictation_active()

        result = await tool.execute(DictationArgs(action="start"))
        assert result.success
        assert is_dictation_active()

        result = await tool.execute(DictationArgs(action="stop"))
        assert result.success
        assert not is_dictation_active()

    @pytest.mark.asyncio
    async def test_start_when_already_active(self):
        tool = DictationTool()
        _set_dictation(True)

        result = await tool.execute(DictationArgs(action="start"))
        assert result.success
        assert "already active" in result.output.lower()
        assert is_dictation_active()

    @pytest.mark.asyncio
    async def test_stop_when_not_active(self):
        tool = DictationTool()
        assert not is_dictation_active()

        result = await tool.execute(DictationArgs(action="stop"))
        assert result.success
        assert "not active" in result.output.lower()
        assert not is_dictation_active()

    @pytest.mark.asyncio
    async def test_double_start_double_stop(self):
        tool = DictationTool()
        await tool.execute(DictationArgs(action="start"))
        await tool.execute(DictationArgs(action="start"))  # idempotent
        assert is_dictation_active()

        await tool.execute(DictationArgs(action="stop"))
        assert not is_dictation_active()
        await tool.execute(DictationArgs(action="stop"))  # idempotent
        assert not is_dictation_active()


# ---------------------------------------------------------------------------
# Special commands
# ---------------------------------------------------------------------------


class TestSpecialCommands:
    def test_period(self):
        assert process_dictation_text("hello period") == "hello."

    def test_comma(self):
        assert process_dictation_text("yes comma indeed") == "yes, indeed"

    def test_new_line_en(self):
        assert process_dictation_text("first line new line second line") == (
            "first line\nsecond line"
        )

    def test_new_line_ru(self):
        assert process_dictation_text("первая строка новая строка вторая строка") == (
            "первая строка\nвторая строка"
        )

    def test_new_line_uk(self):
        assert process_dictation_text("перший рядок новий рядок другий рядок") == (
            "перший рядок\nдругий рядок"
        )

    def test_question_mark(self):
        assert process_dictation_text("really question mark") == "really?"

    def test_exclamation_mark(self):
        assert process_dictation_text("wow exclamation mark") == "wow!"

    def test_semicolon_ru_multi_word(self):
        """'точка с запятой' must match before 'точка'."""
        result = process_dictation_text("один точка с запятой два")
        assert result == "один; два"

    def test_semicolon_uk_multi_word(self):
        result = process_dictation_text("один крапка з комою два")
        assert result == "один; два"

    def test_colon(self):
        assert process_dictation_text("note colon") == "note:"

    def test_tab(self):
        assert process_dictation_text("column one tab column two") == (
            "column one\tcolumn two"
        )

    def test_mixed_text_unchanged(self):
        """Plain text without any commands passes through unchanged."""
        text = "hello world this is a normal sentence"
        assert process_dictation_text(text) == text

    def test_multiple_commands_in_one_sentence(self):
        result = process_dictation_text(
            "dear sir comma new line thank you period"
        )
        assert result == "dear sir,\nthank you."

    def test_ru_period_alone(self):
        assert process_dictation_text("точка") == "."

    def test_ru_comma_alone(self):
        assert process_dictation_text("запятая") == ","

    def test_ru_question_mark(self):
        assert process_dictation_text("как дела вопросительный знак") == "как дела?"

    def test_ru_exclamation_mark(self):
        assert process_dictation_text("ура восклицательный знак") == "ура!"

    def test_uk_period(self):
        assert process_dictation_text("крапка") == "."

    def test_uk_question_mark(self):
        assert process_dictation_text("як справи знак питання") == "як справи?"


# ---------------------------------------------------------------------------
# DICTATION_STOP_PATTERN
# ---------------------------------------------------------------------------


class TestStopPattern:
    @pytest.mark.parametrize("phrase", [
        "stop dictation",
        "end dictation",
        "stop dictating",
        "закончи диктовку",
        "стоп диктовку",
        "останови диктовку",
        "прекрати диктовка",
        "заверши диктовку",
        "зупини диктовку",
        "припини диктовка",
        "стоп диктовка",
    ])
    def test_matches_stop_phrases(self, phrase):
        assert DICTATION_STOP_PATTERN.match(phrase), (
            f"DICTATION_STOP_PATTERN should match: {phrase!r}"
        )

    @pytest.mark.parametrize("phrase", [
        "start dictation",
        "hello world",
        "dictate something",
        "начни диктовку",
        "расскажи мне",
        "stop the music",
        "stop",
    ])
    def test_no_false_positives(self, phrase):
        assert not DICTATION_STOP_PATTERN.match(phrase), (
            f"DICTATION_STOP_PATTERN should NOT match: {phrase!r}"
        )


# ---------------------------------------------------------------------------
# ast.parse — syntax check
# ---------------------------------------------------------------------------


class TestSyntax:
    def test_ast_parse(self):
        import jarvis.tools.local.dictation as _dictation_mod

        src = Path(_dictation_mod.__file__)
        ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
