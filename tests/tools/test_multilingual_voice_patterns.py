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

from jarvis.tools.registry import PRIORITY_CATCH_ALL, VoicePattern


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
        _assert_pattern_matches(patterns[6], phrase)

    def test_uk_what_can_you_do(self, patterns):
        _assert_pattern_matches(patterns[7], "що ти вмієш")
        _assert_pattern_matches(patterns[7], "що ти можеш")


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


# -- play_youtube_music.py -------------------------------------------------


class TestPlayYoutubeMusicPatterns:
    """Voice patterns for play_youtube_music — music dispatch."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
        return PlayYoutubeMusicTool.voice_patterns

    # NOTE: EN has no voice pattern — bare "play X" is ambiguous (song?
    # game? video?) and is routed by the LLM, not a VoicePattern.

    # -- RU patterns (index 0 — with music keyword) ------------------------

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("поставь музыку джаз", "джаз"),
            ("включи музыку рок", "рок"),
            ("запусти песню imagine", "imagine"),
            ("воспроизведи трек believer", "believer"),
            ("включи музыку шопен на ютубе", "шопен"),
        ],
    )
    def test_ru_play_music_with_keyword(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[0], phrase, expected_group=expected_query)
        assert patterns[0].args(m) == {"query": expected_query}

    # -- UK patterns (index 1 — with music keyword) ------------------------

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("постав музику джаз", "джаз"),
            ("увімкни музику рок", "рок"),
            ("запусти пісню imagine", "imagine"),
            ("увімкни трек believer", "believer"),
            ("постав музику шопен на ютубі", "шопен"),
        ],
    )
    def test_uk_play_music_with_keyword(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_query)
        assert patterns[1].args(m) == {"query": expected_query}

    # -- disambiguation: must NOT match app names --------------------------

    def test_ru_no_match_without_music_keyword(self, patterns):
        """'включи хром' has no music keyword — must NOT match music patterns."""
        _assert_no_match(patterns[0], "включи хром")
        _assert_no_match(patterns[0], "включи джаз")
        _assert_no_match(patterns[0], "включи стим")

    def test_uk_no_match_without_music_keyword(self, patterns):
        """'увімкни хром' has no music keyword — must NOT match music patterns."""
        _assert_no_match(patterns[1], "увімкни хром")
        _assert_no_match(patterns[1], "увімкни джаз")


# -- launch_steam_game.py --------------------------------------------------


class TestLaunchSteamGamePatterns:
    """Voice patterns for launch_steam_game — Steam game dispatch."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool
        return LaunchSteamGameTool.voice_patterns

    # -- EN patterns with "steam" keyword ----------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("launch elden ring on steam", "elden ring"),
            ("start portal 2 in steam", "portal 2"),
            ("boot up cyberpunk from steam", "cyberpunk"),
            ("run dota 2 on steam", "dota 2"),
        ],
    )
    def test_en_launch_game_on_steam(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[0], phrase, expected_group=expected_game)
        assert patterns[0].args(m) == {"game_name": expected_game}

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("play cyberpunk on steam", "cyberpunk"),
            ("play portal 2 in steam", "portal 2"),
        ],
    )
    def test_en_play_game_on_steam(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_game)
        assert patterns[1].args(m) == {"game_name": expected_game}

    # -- RU patterns with "стим" keyword -----------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("запусти сталкер в стиме", "сталкер"),
            ("включи киберпанк из стима", "киберпанк"),
            ("загрузи портал на стим", "портал"),
            ("запусти доту в стим", "доту"),
        ],
    )
    def test_ru_launch_game_in_steam(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=expected_game)
        assert patterns[2].args(m) == {"game_name": expected_game}

    # -- RU patterns with "игру" keyword -----------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("запусти игру сталкер", "сталкер"),
            ("включи игру портал 2", "портал 2"),
        ],
    )
    def test_ru_launch_game_with_keyword(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[3], phrase, expected_group=expected_game)
        assert patterns[3].args(m) == {"game_name": expected_game}

    # -- UK patterns with "стім" keyword -----------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("запусти сталкер в стімі", "сталкер"),
            ("увімкни кіберпанк із стіму", "кіберпанк"),
            ("завантаж портал в стім", "портал"),
        ],
    )
    def test_uk_launch_game_in_steam(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[4], phrase, expected_group=expected_game)
        assert patterns[4].args(m) == {"game_name": expected_game}

    # -- UK patterns with "гру" keyword ------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_game",
        [
            ("запусти гру сталкер", "сталкер"),
            ("увімкни гру портал 2", "портал 2"),
        ],
    )
    def test_uk_launch_game_with_keyword(self, patterns, phrase, expected_game):
        m = _assert_pattern_matches(patterns[5], phrase, expected_group=expected_game)
        assert patterns[5].args(m) == {"game_name": expected_game}

    # -- disambiguation: must NOT match bare app names ---------------------

    def test_en_no_match_without_steam(self, patterns):
        """'launch chrome' has no steam keyword — must NOT match."""
        _assert_no_match(patterns[0], "launch chrome")
        _assert_no_match(patterns[1], "play pink floyd")

    def test_ru_no_match_without_steam(self, patterns):
        """'запусти хром' has no стим keyword — must NOT match."""
        _assert_no_match(patterns[2], "запусти хром")


# -- close_app.py ----------------------------------------------------------


class TestCloseAppPatterns:
    """Voice patterns for close_app — close/quit/kill apps."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.close_app import CloseAppTool
        return CloseAppTool.voice_patterns

    # -- EN patterns -------------------------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_name",
        [
            ("close chrome", "chrome"),
            ("quit spotify", "spotify"),
            ("exit notepad", "notepad"),
            ("kill discord", "discord"),
        ],
    )
    def test_en_close_app(self, patterns, phrase, expected_name):
        m = _assert_pattern_matches(patterns[0], phrase, expected_group=expected_name)
        assert patterns[0].args(m) == {"name": expected_name}

    # -- RU patterns -------------------------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_name",
        [
            ("закрой хром", "хром"),
            ("выключи спотифай", "спотифай"),
            ("убей дискорд", "дискорд"),
            ("заверши блокнот", "блокнот"),
        ],
    )
    def test_ru_close_app(self, patterns, phrase, expected_name):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_name)
        assert patterns[1].args(m) == {"name": expected_name}

    # -- UK patterns -------------------------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_name",
        [
            ("закрий хром", "хром"),
            ("вимкни спотіфай", "спотіфай"),
            ("заверши блокнот", "блокнот"),
        ],
    )
    def test_uk_close_app(self, patterns, phrase, expected_name):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=expected_name)
        assert patterns[2].args(m) == {"name": expected_name}


# -- weather.py ------------------------------------------------------------


class TestWeatherPatterns:
    """Voice patterns for get_weather — weather queries."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.weather import WeatherTool
        return WeatherTool.voice_patterns

    # -- EN patterns -------------------------------------------------------

    @pytest.mark.parametrize(
        "phrase",
        ["weather", "what's the weather", "what is the weather"],
    )
    def test_en_weather(self, patterns, phrase):
        _assert_pattern_matches(patterns[0], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["how's the weather", "how is the weather"],
    )
    def test_en_how_weather(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    # -- RU patterns -------------------------------------------------------

    @pytest.mark.parametrize("phrase", ["погода", "какая погода", "погоду"])
    def test_ru_weather(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)

    @pytest.mark.parametrize("phrase", ["что с погодой", "что там на погоде"])
    def test_ru_weather_alt(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)

    # -- UK patterns -------------------------------------------------------

    @pytest.mark.parametrize("phrase", ["погода", "яка погода", "погоді"])
    def test_uk_weather(self, patterns, phrase):
        _assert_pattern_matches(patterns[4], phrase)

    @pytest.mark.parametrize("phrase", ["що з погодою", "що там на погоді"])
    def test_uk_weather_alt(self, patterns, phrase):
        _assert_pattern_matches(patterns[5], phrase)

    # -- no-args check: weather patterns produce empty args -----------------

    def test_weather_no_args(self, patterns):
        """All weather patterns must produce empty args (no location)."""
        for p in patterns:
            compiled = re.compile(p.regex)
            # pick the simplest phrase that each pattern matches
            for phrase in ["weather", "погода", "яка погода",
                           "how's the weather", "что с погодой",
                           "що з погодою"]:
                m = compiled.match(phrase)
                if m is not None:
                    assert p.args(m) == {}, (
                        f"weather pattern {p.regex!r} should produce empty args"
                    )
                    break


# -- system_stats.py -------------------------------------------------------


class TestSystemStatsPatterns:
    """Voice patterns for report_cpu_and_memory_percentages."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.system_stats import SystemStatsTool
        return SystemStatsTool.voice_patterns

    @pytest.mark.parametrize(
        "phrase",
        ["system stats", "system status", "system info",
         "cpu usage", "memory usage", "ram usage"],
    )
    def test_en_system_stats(self, patterns, phrase):
        _assert_pattern_matches(patterns[0], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["статистика системы", "загрузка системы", "состояние пк",
         "статистику системы", "загрузка компьютера"],
    )
    def test_ru_system_stats(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["статистика системи", "завантаження системи",
         "статистику системи", "завантаження пк"],
    )
    def test_uk_system_stats(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


# -- clipboard.py ----------------------------------------------------------


class TestClipboardPatterns:
    """Voice patterns for clipboard — read clipboard contents."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.clipboard import ClipboardTool
        return ClipboardTool.voice_patterns

    @pytest.mark.parametrize(
        "phrase",
        ["clipboard", "what's on my clipboard", "what is in my clipboard",
         "what's in clipboard"],
    )
    def test_en_clipboard(self, patterns, phrase):
        _assert_pattern_matches(patterns[0], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["что в буфере", "прочитай буфер", "буфер обмена"],
    )
    def test_ru_clipboard(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["що в буфері", "прочитай буфер", "буфер обміну"],
    )
    def test_uk_clipboard(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


# -- disambiguation integration -------------------------------------------


class TestDisambiguationPriority:
    """Verify priority ordering prevents cross-tool match collisions.

    These tests check that the priority values on the patterns produce
    the correct winner for ambiguous-looking utterances. The catalogue
    is sorted by priority (lower = checked first), so a match at
    priority 90 (weather) beats a match at priority 110 (close_app).
    """

    def test_music_keyword_beats_open_app(self):
        """'включи музыку джаз' → play_youtube_music, NOT open_app."""
        from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
        from jarvis.tools.local.open_app import OpenAppTool

        for mp in PlayYoutubeMusicTool.voice_patterns:
            assert mp.priority < PRIORITY_CATCH_ALL

    def test_steam_keyword_beats_open_app(self):
        """'запусти сталкер в стиме' → launch_steam_game, NOT open_app."""
        from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool
        from jarvis.tools.local.open_app import OpenAppTool

        for gp in LaunchSteamGameTool.voice_patterns:
            assert gp.priority < PRIORITY_CATCH_ALL

    def test_close_beats_open_app(self):
        """close_app patterns share PRIORITY_CATCH_ALL with open_app;
        disambiguation is regex-based, not priority-based."""
        from jarvis.tools.local.close_app import CloseAppTool

        for cp in CloseAppTool.voice_patterns:
            assert cp.priority == PRIORITY_CATCH_ALL

    def test_weather_is_highest(self):
        """Weather at priority 90 is the highest among these new patterns."""
        from jarvis.tools.local.weather import WeatherTool

        for wp in WeatherTool.voice_patterns:
            assert wp.priority == 90

    def test_no_broad_catch_for_ambiguous_verbs(self):
        """'включи хром' must NOT match music or steam patterns.

        Music patterns require a keyword (музыку/песню/трек).
        Steam patterns require 'стим' or 'игру'.
        So 'включи хром' falls through to open_app at PRIORITY_CATCH_ALL.
        """
        from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
        from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool

        for p in PlayYoutubeMusicTool.voice_patterns:
            _assert_no_match(p, "включи хром")
        for p in LaunchSteamGameTool.voice_patterns:
            _assert_no_match(p, "включи хром")

    def test_catalogue_order_is_priority_sorted(self):
        """The catalogue must return patterns sorted by priority."""
        from jarvis.tools.catalogue import voice_pattern_catalogue

        priorities = [vp.priority for _, vp in voice_pattern_catalogue()]
        assert priorities == sorted(priorities), (
            "voice_pattern_catalogue() must be sorted by priority"
        )


# -- type_into_active_window.py --------------------------------------------


class TestTypeIntoActiveWindowMultilingual:
    """Voice patterns for type_into_active_window: EN/RU/UK dictation mode."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.type_into_active_window import (
            TypeIntoActiveWindowTool,
        )
        return TypeIntoActiveWindowTool.voice_patterns

    # -- EN patterns (indices 0, 1, 2) ------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("type hello world", "hello world"),
            ("type my email address", "my email address"),
            ("type some random text here", "some random text here"),
        ],
    )
    def test_en_type(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[0], phrase, expected_group=expected_text)
        assert patterns[0].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("write meeting notes", "meeting notes"),
            ("write hello there", "hello there"),
        ],
    )
    def test_en_write(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_text)
        assert patterns[1].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("enter my password", "my password"),
            ("enter the code 1234", "the code 1234"),
        ],
    )
    def test_en_enter(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=expected_text)
        assert patterns[2].args(m) == {"text": expected_text}

    # -- RU patterns (indices 3, 4, 5) ------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u043d\u0430\u043f\u0435\u0447\u0430\u0442\u0430\u0439 \u043f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440", "\u043f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440"),
            ("\u043d\u0430\u043f\u0435\u0447\u0430\u0442\u0430\u0439 \u043c\u043e\u0439 \u0435\u043c\u0435\u0439\u043b", "\u043c\u043e\u0439 \u0435\u043c\u0435\u0439\u043b"),
        ],
    )
    def test_ru_napechataj(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[3], phrase, expected_group=expected_text)
        assert patterns[3].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u043d\u0430\u043f\u0438\u0448\u0438 \u043f\u0440\u0438\u0432\u0435\u0442", "\u043f\u0440\u0438\u0432\u0435\u0442"),
            ("\u043d\u0430\u043f\u0438\u0448\u0438 \u0434\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e", "\u0434\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e"),
        ],
    )
    def test_ru_napishi(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[4], phrase, expected_group=expected_text)
        assert patterns[4].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u0432\u0432\u0435\u0434\u0438 \u043f\u0430\u0440\u043e\u043b\u044c 123", "\u043f\u0430\u0440\u043e\u043b\u044c 123"),
            ("\u0432\u0432\u0435\u0434\u0438 \u0442\u0435\u043a\u0441\u0442", "\u0442\u0435\u043a\u0441\u0442"),
        ],
    )
    def test_ru_vvedi(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[5], phrase, expected_group=expected_text)
        assert patterns[5].args(m) == {"text": expected_text}

    # -- UK patterns (indices 6, 7, 8) ------------------------------------

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u043d\u0430\u0434\u0440\u0443\u043a\u0443\u0439 \u043f\u0440\u0438\u0432\u0456\u0442 \u0441\u0432\u0456\u0442", "\u043f\u0440\u0438\u0432\u0456\u0442 \u0441\u0432\u0456\u0442"),
            ("\u043d\u0430\u0434\u0440\u0443\u043a\u0443\u0439 \u043c\u0456\u0439 \u0435\u043c\u0435\u0439\u043b", "\u043c\u0456\u0439 \u0435\u043c\u0435\u0439\u043b"),
        ],
    )
    def test_uk_nadrukuj(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[6], phrase, expected_group=expected_text)
        assert patterns[6].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u043d\u0430\u043f\u0438\u0448\u0438 \u043f\u0440\u0438\u0432\u0456\u0442", "\u043f\u0440\u0438\u0432\u0456\u0442"),
            ("\u043d\u0430\u043f\u0438\u0448\u0438 \u0434\u043e\u0431\u0440\u0438\u0439 \u0440\u0430\u043d\u043e\u043a", "\u0434\u043e\u0431\u0440\u0438\u0439 \u0440\u0430\u043d\u043e\u043a"),
        ],
    )
    def test_uk_napyshi(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[7], phrase, expected_group=expected_text)
        assert patterns[7].args(m) == {"text": expected_text}

    @pytest.mark.parametrize(
        "phrase, expected_text",
        [
            ("\u0432\u0432\u0435\u0434\u0438 \u043f\u0430\u0440\u043e\u043b\u044c", "\u043f\u0430\u0440\u043e\u043b\u044c"),
            ("\u0432\u0432\u0435\u0434\u0438 \u043a\u043e\u0434 5678", "\u043a\u043e\u0434 5678"),
        ],
    )
    def test_uk_vvedy(self, patterns, phrase, expected_text):
        m = _assert_pattern_matches(patterns[8], phrase, expected_group=expected_text)
        assert patterns[8].args(m) == {"text": expected_text}

    # -- negative tests: must NOT match bare verbs or wrong tools ----------

    def test_bare_type_no_match(self, patterns):
        """'type' alone (no text) must not match."""
        _assert_no_match(patterns[0], "type")

    def test_bare_napechataj_no_match(self, patterns):
        _assert_no_match(patterns[3], "\u043d\u0430\u043f\u0435\u0447\u0430\u0442\u0430\u0439")

    def test_zapishi_does_not_match_type_pattern(self, patterns):
        """\"\u0437\u0430\u043f\u0438\u0448\u0438 X\" is note-taking, not typing — must not match here."""
        for p in patterns:
            _assert_no_match(p, "\u0437\u0430\u043f\u0438\u0448\u0438 \u043a\u0443\u043f\u0438\u0442\u044c \u043c\u043e\u043b\u043e\u043a\u043e")

    # -- priority ordering -------------------------------------------------

    def test_all_priorities_in_catch_all_band(self, patterns):
        """All type_into_active_window patterns live at 800-810,
        well above notes (440), auto_login (200), and below CATCH_ALL (900)."""
        for p in patterns:
            assert 800 <= p.priority <= 810, (
                f"priority {p.priority} outside expected 800..810 band"
            )

    def test_notes_beats_type_patterns(self):
        """notes_tools \"запиши\" at 440 beats type \"напиши\" at 800."""
        from jarvis.tools.local.notes_tools import TakeNoteTool
        from jarvis.tools.local.type_into_active_window import (
            TypeIntoActiveWindowTool,
        )

        notes_max = max(p.priority for p in TakeNoteTool.voice_patterns)
        type_min = min(p.priority for p in TypeIntoActiveWindowTool.voice_patterns)
        assert notes_max < type_min, (
            f"notes priority {notes_max} must be < type priority {type_min}"
        )


# -- deep_research_tools.py ------------------------------------------------


class TestDeepResearchMultilingual:
    """RU/UK voice patterns for DeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import DeepResearchTool
        return DeepResearchTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        m = _assert_pattern_matches(
            patterns[0], "deep research quantum computing",
            expected_group="quantum computing",
        )
        assert patterns[0].args(m) == {"query": "quantum computing"}

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("глубокое исследование квантовые компьютеры", "квантовые компьютеры"),
            ("глубокое исследование на тему ии", "ии"),
            ("исследуй подробно машинное обучение", "машинное обучение"),
        ],
    )
    def test_ru_deep_research(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_query)
        assert patterns[1].args(m) == {"query": expected_query}

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("глибоке дослідження квантові комп'ютери", "квантові комп'ютери"),
            ("глибоке дослідження на тему шi", "шi"),
            ("дослідж детально машинне навчання", "машинне навчання"),
        ],
    )
    def test_uk_deep_research(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=expected_query)
        assert patterns[2].args(m) == {"query": expected_query}


class TestPauseDeepResearchMultilingual:
    """RU/UK voice patterns for PauseDeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import PauseDeepResearchTool
        return PauseDeepResearchTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "pause deep research")

    @pytest.mark.parametrize(
        "phrase",
        [
            "пауза исследование",
            "поставь на паузу исследование",
            "поставь на паузу глубокое исследование",
        ],
    )
    def test_ru_pause(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "пауза дослідження",
            "постав на паузу дослідження",
            "постав на паузу глибоке дослідження",
        ],
    )
    def test_uk_pause(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


class TestResumeDeepResearchMultilingual:
    """RU/UK voice patterns for ResumeDeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import ResumeDeepResearchTool
        return ResumeDeepResearchTool.voice_patterns

    def test_en_patterns_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "resume deep research")
        _assert_pattern_matches(patterns[1], "continue deep research")

    @pytest.mark.parametrize(
        "phrase",
        ["продолжи исследование", "продолжи глубокое исследование"],
    )
    def test_ru_resume(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["продовж дослідження", "продовж глибоке дослідження"],
    )
    def test_uk_resume(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)


class TestCloseDeepResearchMultilingual:
    """RU/UK voice patterns for CloseDeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import CloseDeepResearchTool
        return CloseDeepResearchTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "close deep research")

    @pytest.mark.parametrize(
        "phrase",
        ["закрой исследование", "закрой глубокое исследование"],
    )
    def test_ru_close(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        ["закрий дослідження", "закрий глибоке дослідження"],
    )
    def test_uk_close(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


class TestDeleteDeepResearchMultilingual:
    """RU/UK voice patterns for DeleteDeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import DeleteDeepResearchTool
        return DeleteDeepResearchTool.voice_patterns

    def test_en_query_pattern_unchanged(self, patterns):
        m = _assert_pattern_matches(
            patterns[0], "delete deep research on quantum computing",
            expected_group="quantum computing",
        )
        assert patterns[0].args(m) == {"query": "quantum computing"}

    def test_en_bare_pattern_unchanged(self, patterns):
        m = _assert_pattern_matches(patterns[3], "delete deep research")
        assert patterns[3].args(m) == {"query": ""}

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("удали исследование квантовые компьютеры", "квантовые компьютеры"),
            ("удали исследование на тему ии", "ии"),
            ("удали исследование про блокчейн", "блокчейн"),
            ("удали глубокое исследование ии", "ии"),
        ],
    )
    def test_ru_delete_query(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[1], phrase, expected_group=expected_query)
        assert patterns[1].args(m) == {"query": expected_query}

    @pytest.mark.parametrize(
        "phrase, expected_query",
        [
            ("видали дослідження квантові комп'ютери", "квантові комп'ютери"),
            ("видали дослідження на тему шi", "шi"),
            ("видали глибоке дослідження блокчейн", "блокчейн"),
        ],
    )
    def test_uk_delete_query(self, patterns, phrase, expected_query):
        m = _assert_pattern_matches(patterns[2], phrase, expected_group=expected_query)
        assert patterns[2].args(m) == {"query": expected_query}

    @pytest.mark.parametrize(
        "phrase",
        ["удали исследование", "удали глубокое исследование"],
    )
    def test_ru_delete_bare(self, patterns, phrase):
        m = _assert_pattern_matches(patterns[4], phrase)
        assert patterns[4].args(m) == {"query": ""}

    @pytest.mark.parametrize(
        "phrase",
        ["видали дослідження", "видали глибоке дослідження"],
    )
    def test_uk_delete_bare(self, patterns, phrase):
        m = _assert_pattern_matches(patterns[5], phrase)
        assert patterns[5].args(m) == {"query": ""}


class TestDeleteAllDeepResearchMultilingual:
    """RU/UK voice patterns for DeleteAllDeepResearchTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_tools import DeleteAllDeepResearchTool
        return DeleteAllDeepResearchTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "delete all deep research")
        _assert_pattern_matches(patterns[0], "clear all deep research history")

    @pytest.mark.parametrize(
        "phrase",
        [
            "удали все исследования",
            "удали все глубокие исследования",
            "удали все исследования историю",
        ],
    )
    def test_ru_delete_all(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "видали всі дослідження",
            "видали всі глибокі дослідження",
            "видали всі дослідження історію",
        ],
    )
    def test_uk_delete_all(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


# -- deep_research_ultra_tools.py ------------------------------------------


class TestEnableDeepResearchUltraMultilingual:
    """RU/UK voice patterns for EnableDeepResearchUltraTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_ultra_tools import (
            EnableDeepResearchUltraTool,
        )
        return EnableDeepResearchUltraTool.voice_patterns

    def test_en_pattern_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "enable ultra")
        _assert_pattern_matches(patterns[0], "turn on ultra research")

    @pytest.mark.parametrize(
        "phrase",
        [
            "включи ультра",
            "включи ультра режим",
            "включи ультра исследование",
            "включи ультра режим исследования",
            "активируй ультра",
        ],
    )
    def test_ru_enable_ultra(self, patterns, phrase):
        _assert_pattern_matches(patterns[1], phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "увімкни ультра",
            "увімкни ультра режим",
            "увімкни ультра дослідження",
            "увімкни ультра режим дослідження",
            "активуй ультра",
        ],
    )
    def test_uk_enable_ultra(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)


class TestDisableDeepResearchUltraMultilingual:
    """RU/UK voice patterns for DisableDeepResearchUltraTool."""

    @pytest.fixture()
    def patterns(self):
        from jarvis.tools.local.deep_research_ultra_tools import (
            DisableDeepResearchUltraTool,
        )
        return DisableDeepResearchUltraTool.voice_patterns

    def test_en_patterns_unchanged(self, patterns):
        _assert_pattern_matches(patterns[0], "disable ultra")
        _assert_pattern_matches(patterns[1], "normal deep research")

    @pytest.mark.parametrize(
        "phrase",
        [
            "выключи ультра",
            "выключи ультра режим",
            "выключи ультра исследование",
            "отключи ультра",
        ],
    )
    def test_ru_disable_ultra(self, patterns, phrase):
        _assert_pattern_matches(patterns[2], phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "вимкни ультра",
            "вимкни ультра режим",
            "вимкни ультра дослідження",
            "відключи ультра",
        ],
    )
    def test_uk_disable_ultra(self, patterns, phrase):
        _assert_pattern_matches(patterns[3], phrase)
