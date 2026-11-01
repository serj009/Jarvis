"""Pin game-launch voice-pattern routing across the Task 8c commit-4 change.

Two phases live in one file so the equivalence is auditable:

* The "игру / гру" patterns (priority 108) currently route to
  launch_steam_game. Task 8c commit 4 moves them to the new launch_game
  (unified Steam+Epic+GOG index) while the explicit "on steam / в стиме"
  patterns stay on launch_steam_game. These tests pin the *name parsing*
  (which game string is extracted) and the *priority/catch-all ordering*
  so the move cannot silently change what gets resolved — only which tool
  receives the identical args.

Run order matters for review: before the move, GAME_WORD_EXPECTED_TOOL is
"launch_steam_game"; the commit flips it to "launch_game" in the same PR,
and every name-extraction / ordering assertion stays byte-for-byte.
"""

from __future__ import annotations

from typing import cast

import pytest

from jarvis.llm.conversation import Conversation
from jarvis.llm.intent_router import IntentRouter, ToolIntent
from jarvis.llm.ollama_client import OllamaClient


def fixed_time_provider():
    import datetime

    return datetime.datetime(2026, 1, 1, 12, 0, 0)


def _router() -> IntentRouter:
    """Pattern layer in isolation (no registry gate — every pattern
    reachable), mirroring test_router_pattern_equivalence."""
    return IntentRouter(
        llm=cast(OllamaClient, object()),
        conversation=cast(Conversation, object()),
        time_provider=fixed_time_provider,
    )


# The tool that the generic "игру / гру / the game" phrases route to.
# After the Task 8c commit-4 move, the generic "игру / гру / the game"
# phrases route to launch_game (unified index). Name extraction and
# priority/catch-all ordering are byte-for-byte unchanged from the
# pre-move baseline — that invariance is the equivalence guarantee.
GAME_WORD_EXPECTED_TOOL = "launch_game"


# --- name extraction: the game string must be parsed identically --------


@pytest.mark.parametrize(
    ("utterance", "expected_game"),
    [
        # RU "запусти/включи игру X"
        ("запусти игру ведьмак", "ведьмак"),
        ("включи игру portal 2", "portal 2"),
        ("запусти игру elden ring", "elden ring"),
        # UK "запусти/увімкни гру X"
        ("запусти гру відьмак", "відьмак"),
        ("увімкни гру control", "control"),
    ],
)
def test_game_word_patterns_extract_game_name(utterance: str, expected_game: str):
    intent = _router()._try_pattern(utterance)
    assert isinstance(intent, ToolIntent), f"{utterance!r} should match a pattern"
    assert intent.tool_name == GAME_WORD_EXPECTED_TOOL
    assert intent.args.get("game_name") == expected_game


# --- explicit "on steam / в стиме" stays on launch_steam_game -----------


@pytest.mark.parametrize(
    ("utterance", "expected_game"),
    [
        ("launch elden ring on steam", "elden ring"),
        ("play cyberpunk on steam", "cyberpunk"),
        ("запусти сталкер в стиме", "сталкер"),
        ("запусти відьмак в стімі", "відьмак"),
    ],
)
def test_on_steam_patterns_stay_on_launch_steam_game(utterance, expected_game):
    intent = _router()._try_pattern(utterance)
    assert isinstance(intent, ToolIntent)
    assert intent.tool_name == "launch_steam_game"
    assert intent.args.get("game_name") == expected_game


# --- priority / catch-all ordering (condition 3) ------------------------


def test_game_word_beats_generic_open_app_catch_all():
    """"запусти игру X" must be claimed by the game pattern (priority 108),
    NOT by the open_app catch-all (priority 900). If this flips, a game
    request would try to launch an app literally named "игру X"."""
    intent = _router()._try_pattern("запусти игру ведьмак")
    assert isinstance(intent, ToolIntent)
    assert intent.tool_name == GAME_WORD_EXPECTED_TOOL
    # the catch-all would have produced open_app with name="игру ведьмак"
    assert intent.tool_name != "open_app"


def test_plain_launch_x_without_game_word_is_open_app():
    """"запусти стим" (no "игру") is an app launch, not a game launch —
    the generic open_app catch-all claims it."""
    intent = _router()._try_pattern("запусти стим")
    assert isinstance(intent, ToolIntent)
    assert intent.tool_name == "open_app"
    assert intent.args.get("name") == "стим"


def test_game_word_with_app_name_is_a_game_not_the_app():
    """Collision case (condition 3): "запусти игру стим" carries the game
    keyword, so it is a GAME request with game_name="стим" — it does NOT
    resolve to the Steam *application*. Resolution of that title is left
    to the launcher index (likely a near-miss at run time), but the
    ROUTING is unambiguously the game tool."""
    intent = _router()._try_pattern("запусти игру стим")
    assert isinstance(intent, ToolIntent)
    assert intent.tool_name == GAME_WORD_EXPECTED_TOOL
    assert intent.args.get("game_name") == "стим"
    assert intent.tool_name != "open_app"
