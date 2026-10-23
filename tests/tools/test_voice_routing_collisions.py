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
    # Steam games -> launch_steam_game.
    ("запусти сталкер в стиме", "launch_steam_game"),
    ("запусти игру ведьмак", "launch_steam_game"),
    # Whitelisted shell commands -> run_command (cli_runner).
    ("запусти ping 127.0.0.1", "run_command"),
    ("run dir", "run_command"),
    ("выполни whoami", "run_command"),
    # Test / script execution -> code_runner.
    ("запусти тесты", "code_runner"),
    # Protocols -> protocol tool (most specific, lowest priority number).
    ("запусти протокол вечеринка", "run_protocol"),
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
