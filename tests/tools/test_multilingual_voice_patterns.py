"""Tests for RU/UK multilingual voice patterns.

Validates that every RU/UK voice pattern:
1. Compiles without error (re.compile)
2. Matches the expected sample phrases
3. Captures the correct groups where applicable
4. Maps volume action words to the correct EN actions
5. Does NOT break existing EN patterns (they are unchanged)
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import pytest

from jarvis.tools.registry import VoicePattern


# -- helpers ---------------------------------------------------------------


def _assert_pattern_matches(
    pattern: VoicePattern,
    text: str,
    *,
    expected_group: str | None = None,
    group_index: int = 1,
) -> re.Match[str]:
    """Assert ``pattern.regex`` matches ``text`` and optionally check a
    captured group. Returns the match object for further inspection."""
    compiled = re.compile(pattern.regex)
    m = compiled.match(text)
    assert m is not None, (
        f"pattern {pattern.regex!r} did not match {text!r}"
    )
    if expected_group is not None:
        assert m.group(group_index) == expected_group, (
            f"group({group_index}) = {m.group(group_index)!r}, "
            f"expected {expected_group!r}"
        )
    return m


def _assert_no_match(pattern: VoicePattern, text: str) -> None:
    """Assert ``pattern.regex`` does NOT match ``text``."""
    compiled = re.compile(pattern.regex)
    assert compiled.match(text) is None, (
        f"pattern {pattern.regex!r} should NOT match {text!r}"
    )


# -- pattern compilation --------------------------------------------------


class TestAllPatternsCompile:
    """Every VoicePattern.regex in the catalogue must re.compile cleanly."""

    def test_all_voice_patterns_compile(self) -> None:
        from jarvis.tools.catalogue import voice_pattern_catalogue

        for tool_name, vp in voice_pattern_catalogue():
            try:
                re.compile(vp.regex)
            except re.error as exc:
                pytest.fail(
                    f"tool={tool_name!r} regex={vp.regex!r} "
                    f"failed to compile: {exc}"
                )


# -- open_app.py -----------------------------------------------------------


class TestOpenAppMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.open_app import OpenAppTool
        return OpenAppTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "open chrome", expected_group="chrome")

    def test_ru_open(self, patterns):
        _assert_pattern_matches(patterns[1], "открой хром", expected_group="хром")
        _assert_pattern_matches(patterns[1], "запусти блокнот", expected_group="блокнот")
        _assert_pattern_matches(patterns[1], "открыть калькулятор", expected_group="калькулятор")

    def test_uk_open(self, patterns):
        _assert_pattern_matches(patterns[2], "відкрий хром", expected_group="хром")
        _assert_pattern_matches(patterns[2], "запусти блокнот", expected_group="блокнот")
        _assert_pattern_matches(patterns[2], "відкрити калькулятор", expected_group="калькулятор")


# -- volume.py -------------------------------------------------------------


class TestVolumeMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.volume import VolumeTool
        return VolumeTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        m = _assert_pattern_matches(patterns[0], "volume up")
        assert patterns[0].args(m) == {"action": "up"}

    @pytest.mark.parametrize(
        "phrase, expected_action",
        [
            ("звук громче", "up"),
            ("громкость тише", "down"),
            ("звук выключи", "mute"),
            ("звук выключить", "mute"),
            ("звук включи", "unmute"),
            ("громкость включить", "unmute"),
        ],
    )
    def test_ru_volume(self, patterns, phrase, expected_action):
        m = _assert_pattern_matches(patterns[1], phrase)
        assert patterns[1].args(m)["action"] == expected_action

    @pytest.mark.parametrize(
        "phrase, expected_action",
        [
            ("звук гучніше", "up"),
            ("гучність тихіше", "down"),
            ("звук вимкни", "mute"),
            ("звук вимкнути", "mute"),
            ("гучність увімкни", "unmute"),
            ("звук увімкнути", "unmute"),
        ],
    )
    def test_uk_volume(self, patterns, phrase, expected_action):
        m = _assert_pattern_matches(patterns[2], phrase)
        assert patterns[2].args(m)["action"] == expected_action


# -- screenshot.py ---------------------------------------------------------


class TestScreenshotMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.screenshot import ScreenshotTool
        return ScreenshotTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "screenshot")
        _assert_pattern_matches(patterns[0], "take screenshot")
        _assert_pattern_matches(patterns[0], "take a screenshot")

    @pytest.mark.parametrize("phrase", ["скриншот", "сделай скриншот"])
    def test_ru_screenshot(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    def test_ru_screen_capture(self, patterns):
        _assert_pattern_matches(patterns[2], "снимок экрана")

    @pytest.mark.parametrize("phrase", ["скріншот", "зроби скріншот"])
    def test_uk_screenshot(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)

    @pytest.mark.parametrize("phrase", ["знімок екрану", "знімок екрана"])
    def test_uk_screen_capture(self, patterns, phrase):
        _assert_pattern_matches(patterns[4], phrase)


# -- lock_screen.py --------------------------------------------------------


class TestLockScreenMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.lock_screen import LockScreenTool
        return LockScreenTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "lock the screen")
        _assert_pattern_matches(patterns[0], "lock screen")
        _assert_pattern_matches(patterns[0], "lock my pc")

    @pytest.mark.parametrize(
        "phrase",
        ["заблокируй экран", "заблокируй компьютер", "заблокируй пк"],
    )
    def test_ru_lock(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize("phrase", ["заблокировать экран", "заблокировать компьютер"])
    def test_ru_lock_infinitive(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["заблокуй екран", "заблокуй комп'ютер", "заблокуй компютер", "заблокуй пк"],
    )
    def test_uk_lock(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)


# -- open_url.py -----------------------------------------------------------


class TestOpenUrlMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.open_url import OpenUrlTool
        return OpenUrlTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        m = _assert_pattern_matches(patterns[0], "search python tutorial")
        url = patterns[0].args(m)["url"]
        assert "google.com/search" in url
        assert "python+tutorial" in url

    @pytest.mark.parametrize(
        "phrase, query",
        [
            ("найди рецепт борща", "рецепт борща"),
            ("загугли погоду", "погоду"),
            ("поищи новости", "новости"),
            ("погугли python tutorial", "python tutorial"),
        ],
    )
    def test_ru_search(self, patterns, phrase, query):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=query)
        url = patterns[1].args(m)["url"]
        assert "google.com/search" in url

    @pytest.mark.parametrize(
        "phrase, query",
        [
            ("знайди рецепт борщу", "рецепт борщу"),
            ("загугли погоду", "погоду"),
            ("пошукай новини", "новини"),
            ("погугли python tutorial", "python tutorial"),
        ],
    )
    def test_uk_search(self, patterns, phrase, query):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=query)
        url = patterns[2].args(m)["url"]
        assert "google.com/search" in url


# -- launch_workspace.py ---------------------------------------------------


class TestLaunchWorkspaceMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.launch_workspace import LaunchWorkspaceTool
        return LaunchWorkspaceTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "open my workspace")
        _assert_pattern_matches(patterns[0], "launch workspace")

    @pytest.mark.parametrize(
        "phrase",
        ["открой рабочее", "запусти рабочий", "открой мой workspace"],
    )
    def test_ru_workspace(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["відкрий робоче", "запусти робочий", "відкрий моє робоче"],
    )
    def test_uk_workspace(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


# -- see_screen.py ---------------------------------------------------------


class TestSeeScreenMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.see_screen import SeeScreenTool
        return SeeScreenTool.voice_patterns

    def test_en_patterns_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "look at my screen")
        _assert_pattern_matches(patterns[1], "what's on my screen")

    @pytest.mark.parametrize(
        "phrase",
        [
            "посмотри на экран",
            "опиши экран",
            "что на экране",
            "что на моём экране",
        ],
    )
    def test_ru_see_screen(self, patterns, phrase):
        _assert_pattern_matches(patterns[4], phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "подивись на екран",
            "опиши екран",
            "що на екрані",
            "що на моєму екрані",
        ],
    )
    def test_uk_see_screen(self, patterns, phrase):
        _assert_pattern_matches(patterns[5], phrase)


# -- notes_tools.py --------------------------------------------------------


class TestNotesMultilingual:
    def test_ru_take_note(self):
        from jarvis.tools.local.notes_tools import TakeNoteTool
        patterns = TakeNoteTool.voice_patterns
        p = patterns[1]  # RU
        _assert_pattern_matches(p, "запиши купить молоко", expected_group="купить молоко")
        _assert_pattern_matches(p, "заметка: встреча в 3", expected_group="встреча в 3")
        _assert_pattern_matches(p, "запомни номер телефона", expected_group="номер телефона")

    def test_uk_take_note(self):
        from jarvis.tools.local.notes_tools import TakeNoteTool
        patterns = TakeNoteTool.voice_patterns
        p = patterns[2]  # UK
        _assert_pattern_matches(p, "запиши купити молоко", expected_group="купити молоко")
        _assert_pattern_matches(p, "нотатка: зустріч о 3", expected_group="зустріч о 3")
        _assert_pattern_matches(p, "запам'ятай номер", expected_group="номер")

    def test_ru_open_notes(self):
        from jarvis.tools.local.notes_tools import OpenNotesTool
        patterns = OpenNotesTool.voice_patterns
        _assert_pattern_matches(patterns[1], "открой заметки")
        _assert_pattern_matches(patterns[1], "покажи мои заметки")

    def test_uk_open_notes(self):
        from jarvis.tools.local.notes_tools import OpenNotesTool
        patterns = OpenNotesTool.voice_patterns
        _assert_pattern_matches(patterns[2], "відкрий нотатки")
        _assert_pattern_matches(patterns[2], "покажи мої нотатки")

    def test_ru_close_notes(self):
        from jarvis.tools.local.notes_tools import CloseNotesTool
        patterns = CloseNotesTool.voice_patterns
        _assert_pattern_matches(patterns[1], "закрой заметки")

    def test_uk_close_notes(self):
        from jarvis.tools.local.notes_tools import CloseNotesTool
        patterns = CloseNotesTool.voice_patterns
        _assert_pattern_matches(patterns[2], "закрий нотатки")


# -- dashboard_tools.py ----------------------------------------------------


class TestDashboardMultilingual:
    def test_ru_show_dashboard(self):
        from jarvis.tools.local.dashboard_tools import ShowDashboardTool
        patterns = ShowDashboardTool.voice_patterns
        _assert_pattern_matches(patterns[2], "покажи панель")
        _assert_pattern_matches(patterns[2], "открой дашборд")
        _assert_pattern_matches(patterns[2], "покажи мою статистику")

    def test_uk_show_dashboard(self):
        from jarvis.tools.local.dashboard_tools import ShowDashboardTool
        patterns = ShowDashboardTool.voice_patterns
        _assert_pattern_matches(patterns[3], "покажи панель")
        _assert_pattern_matches(patterns[3], "відкрий дашборд")

    def test_ru_close_dashboard(self):
        from jarvis.tools.local.dashboard_tools import CloseDashboardTool
        patterns = CloseDashboardTool.voice_patterns
        _assert_pattern_matches(patterns[1], "закрой панель")
        _assert_pattern_matches(patterns[1], "закрой дашборд")

    def test_uk_close_dashboard(self):
        from jarvis.tools.local.dashboard_tools import CloseDashboardTool
        patterns = CloseDashboardTool.voice_patterns
        _assert_pattern_matches(patterns[2], "закрий панель")


# -- help_tools.py ---------------------------------------------------------


class TestHelpMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.help_tools import OpenHelpTool
        return OpenHelpTool.voice_patterns

    def test_en_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "show help")
        _assert_pattern_matches(patterns[2], "what can you do")

    @pytest.mark.parametrize("phrase", ["помощь", "покажи справку", "справка"])
    def test_ru_help(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)

    def test_ru_what_can_you_do(self, patterns):
        _assert_pattern_matches(patterns[4], "что ты умеешь")
        _assert_pattern_matches(patterns[4], "что ты можешь")

    @pytest.mark.parametrize("phrase", ["допомога", "покажи довідку", "довідка"])
    def test_uk_help(self, patterns, phrase):
        _assert_pattern_matches(patterns[5], phrase)

    def test_uk_what_can_you_do(self, patterns):
        _assert_pattern_matches(patterns[6], "що ти вмієш")
        _assert_pattern_matches(patterns[6], "що ти можеш")


# -- research.py -----------------------------------------------------------


class TestResearchMultilingual:
    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.research import ResearchTool
        return ResearchTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "research quantum computing",
                               expected_group="quantum computing")

    def test_ru_research(self, patterns):
        _assert_pattern_matches(
            patterns[2], "найди информацию про ии",
            expected_group="про ии",
        )
        _assert_pattern_matches(
            patterns[2], "исследуй искусственный интеллект",
            expected_group="искусственный интеллект",
        )

    def test_uk_research(self, patterns):
        _assert_pattern_matches(
            patterns[3], "знайди інформацію про шi",
            expected_group="про шi",
        )
        _assert_pattern_matches(
            patterns[3], "досліди штучний інтелект",
            expected_group="штучний інтелект",
        )


# -- log_tools.py ----------------------------------------------------------


class TestLogToolsMultilingual:
    def test_ru_show_logs(self):
        from jarvis.tools.local.log_tools import ShowLogsTool
        patterns = ShowLogsTool.voice_patterns
        _assert_pattern_matches(patterns[2], "покажи логи")
        _assert_pattern_matches(patterns[2], "открой лог")

    def test_uk_show_logs(self):
        from jarvis.tools.local.log_tools import ShowLogsTool
        patterns = ShowLogsTool.voice_patterns
        _assert_pattern_matches(patterns[3], "покажи логи")
        _assert_pattern_matches(patterns[3], "відкрий лог")

    def test_ru_close_logs(self):
        from jarvis.tools.local.log_tools import CloseLogsTool
        patterns = CloseLogsTool.voice_patterns
        _assert_pattern_matches(patterns[1], "закрой логи")
        _assert_pattern_matches(patterns[1], "закрой лог")

    def test_uk_close_logs(self):
        from jarvis.tools.local.log_tools import CloseLogsTool
        patterns = CloseLogsTool.voice_patterns
        _assert_pattern_matches(patterns[2], "закрий логи")


# -- clipboard_history_tools.py --------------------------------------------


class TestClipboardHistoryMultilingual:
    def test_ru_show_clipboard(self):
        from jarvis.tools.local.clipboard_history_tools import ShowClipboardHistoryTool
        patterns = ShowClipboardHistoryTool.voice_patterns
        _assert_pattern_matches(patterns[2], "покажи буфер обмена")
        _assert_pattern_matches(patterns[2], "открой историю буфера обмена")
        _assert_pattern_matches(patterns[2], "покажи буфер")

    def test_uk_show_clipboard(self):
        from jarvis.tools.local.clipboard_history_tools import ShowClipboardHistoryTool
        patterns = ShowClipboardHistoryTool.voice_patterns
        _assert_pattern_matches(patterns[3], "покажи буфер обміну")
        _assert_pattern_matches(patterns[3], "відкрий історію буферу обміну")

    def test_ru_close_clipboard(self):
        from jarvis.tools.local.clipboard_history_tools import CloseClipboardHistoryTool
        patterns = CloseClipboardHistoryTool.voice_patterns
        _assert_pattern_matches(patterns[1], "закрой буфер обмена")

    def test_uk_close_clipboard(self):
        from jarvis.tools.local.clipboard_history_tools import CloseClipboardHistoryTool
        patterns = CloseClipboardHistoryTool.voice_patterns
        _assert_pattern_matches(patterns[2], "закрий буфер обміну")
