"""Voice-routing collision tests (Этап 10).

The router matches voice patterns first-match-wins by ascending priority
(see registry.PRIORITY_* and catalogue.voice_pattern_catalogue). A greedy
``(.+)`` pattern on a common verb ("запусти …", "run …") can silently
steal a phrase meant for another tool. The headline example from the audit:
``cli_runner``'s "запусти X" used to grab "запусти хром" (an app) and
"запусти сталкер в стиме" (a Steam game), then fail the command whitelist.

These tests pin the resolution of a set of representative phrases to the
CORRECT tool, so a future greedy pattern that re-introduces the collision
fails CI.
"""

from __future__ import annotations

import re

import pytest

from jarvis.tools.catalogue import voice_pattern_catalogue


def _route(phrase: str) -> str | None:
    """Return the tool name the router would pick for *phrase*.

    Mirrors the router: patterns are tried in ascending priority
    (voice_pattern_catalogue is already priority-sorted) and the first
    regex that matches wins.
    """
    for tool_name, vp in voice_pattern_catalogue():
        if re.match(vp.regex, phrase, re.IGNORECASE):
            return tool_name
    return None


# (phrase, expected tool name). These are the collision cases Этап 10 names
# plus the neighbours they must not steal from.
_CASES = [
    # App launches must go to open_app, NOT cli_runner / steam / code_runner.
    ("запусти хром", "open_app"),
    ("открой блокнот", "open_app"),
    ("запусти калькулятор", "open_app"),
    # Explicit "в стиме" -> launch_steam_game (legacy, Steam-only).
    ("запусти сталкер в стиме", "launch_steam_game"),
    # Task 8c commit 4: generic "игру X" now -> launch_game (all launchers).
    ("запусти игру ведьмак", "launch_game"),
    # Whitelisted shell commands -> run_command (cli_runner).
    ("запусти ping 127.0.0.1", "run_command"),
    ("run dir", "run_command"),
    ("выполни whoami", "run_command"),
    # Test / script execution -> code_runner.
    ("запусти тесты", "code_runner"),
    # A domain with "в браузере" before or after it is a website, not a
    # program: open_app's "открой (.+)" used to try to launch
    # "в браузере uakino.best" (seen in a live session).
    ("открой uakino.best", "open_url"),
    ("открой в браузере uakino.best", "open_url"),
    ("открой uakino.best в браузере", "open_url"),
    ("відкрий у браузері uakino.best", "open_url"),
    ("open example.com in the browser", "open_url"),
    # Protocols -> protocol tool (most specific, lowest priority number).
    ("запусти протокол вечеринка", "run_protocol"),
    # "Remember the PAGE" reads the page into memory (save_page_knowledge);
    # take_note's "запомни (.+)" used to steal these and save a note reading
    # "эту страницу" without ever opening the page.
    ("запомни эту страницу", "save_page_knowledge"),
    ("сохрани эту страницу", "save_page_knowledge"),
    ("сохрани этот рецепт", "save_page_knowledge"),
    ("запомни гайд с этой страницы", "save_page_knowledge"),
    ("сохрани это прохождение", "save_page_knowledge"),
    ("запомни что на странице", "save_page_knowledge"),
    ("запам'ятай цю сторінку", "save_page_knowledge"),
    ("запамʼятай цю сторінку", "save_page_knowledge"),
    ("запам’ятай цю сторінку", "save_page_knowledge"),
    ("збережи рецепт", "save_page_knowledge"),
    ("remember this page", "save_page_knowledge"),
    ("save this recipe", "save_page_knowledge"),
    # "Save the LINK" -> bookmarks.
    ("сохрани ссылку", "bookmarks"),
    ("запомни эту ссылку", "bookmarks"),
    ("сохрани страницу в закладки", "bookmarks"),
    ("сохрани в закладки", "bookmarks"),
    ("збережи посилання", "bookmarks"),
    ("save this link", "bookmarks"),
    # Ordinary notes stay notes.
    ("запомни купить молоко", "take_note"),
    ("запомни что завтра встреча в три", "take_note"),
    ("remember this the bins go out tuesday", "take_note"),
    ("запам'ятай купити хліб", "take_note"),
    # Neighbours on the same nouns keep their tools.
    ("прочитай эту страницу", "content_reader"),
    ("сохрани страницу как pdf", "content_reader"),
    ("сохрани картинки со страницы", "scrape_images"),
    # Polite forms and "current / open page".
    ("сохраните текущую страницу", "save_page_knowledge"),
    ("запомните эту страницу", "save_page_knowledge"),
    ("сохрани открытую страницу", "save_page_knowledge"),
    ("збережіть поточну сторінку", "save_page_knowledge"),
    ("save the current page", "save_page_knowledge"),
    ("запомните купить хлеб", "take_note"),
    # Using a saved list.
    ("что осталось", "saved_list_status"),
    ("что ещё осталось в блич", "saved_list_status"),
    ("сколько процентов пройдено", "saved_list_status"),
    ("список продуктов для борща", "saved_list_status"),
    ("пересчитай борщ на 6 порций", "saved_list_status"),
    ("що залишилось", "saved_list_status"),
    ("what's left", "saved_list_status"),
    ("отметь крылья феникса выполненным", "mark_list_item"),
    ("сними отметку с король душ", "mark_list_item"),
    ("познач крила фенікса виконаним", "mark_list_item"),
    ("mark phoenix wings as done", "mark_list_item"),
    ("покажи весь список", "saved_list_status"),
    ("покажи рецепт борща", "saved_list_status"),
    ("show the whole list", "saved_list_status"),
    ("покажи мои закладки", "bookmarks"),
    ("show my bookmarks", "bookmarks"),
    # Naming a saved list must beat file_manager's generic rename...
    ("назови этот список блич", "name_saved_list"),
    ("переименуй список блич в бличик", "name_saved_list"),
    ("переименуй этот список в блич", "name_saved_list"),
    ("rename the list bleach to bl", "name_saved_list"),
    # ... while files are still renamed by file_manager.
    ("переименуй файл а в б", "file_manager"),
    ("переименуй папку фото в отпуск", "file_manager"),
    ("rename report.txt to old.txt", "file_manager"),
    # ... and their neighbours keep their tools.
    ("сколько будет 5 процентов от 200", "calculator"),
    ("список таймеров", "list_timers"),
    ("список протоколов", "list_protocols"),
]


@pytest.mark.parametrize("phrase,expected", _CASES)
def test_phrase_routes_to_expected_tool(phrase: str, expected: str) -> None:
    got = _route(phrase)
    assert got == expected, (
        f"{phrase!r} routed to {got!r}, expected {expected!r}"
    )


def test_run_command_does_not_steal_app_launches() -> None:
    """cli_runner must never be the match for a non-whitelisted 'запусти X'."""
    for phrase in ("запусти хром", "запусти сталкер в стиме", "запусти музыку"):
        assert _route(phrase) != "run_command", (
            f"cli_runner stole {phrase!r}"
        )


def test_run_command_still_catches_whitelisted() -> None:
    """A whitelisted command after 'run/запусти' still reaches run_command."""
    for phrase in ("run dir", "запусти ping 8.8.8.8", "выполни systeminfo",
                   "run git status", "запусти pip list"):
        assert _route(phrase) == "run_command", (
            f"{phrase!r} did not reach run_command"
        )
