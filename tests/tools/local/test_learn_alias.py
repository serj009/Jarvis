"""Tests for jarvis.tools.local.learn_alias (Task 8b-3).

PersonalAliasStore writes an encrypted JSON file under JARVIS_DATA_DIR,
which the autouse conftest fixture points at a temp dir; DPAPI is a no-op
in tests. So these exercise the real learn/resolve round-trip.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from jarvis.memory.personal_aliases import PersonalAliasStore
from jarvis.tools.local.learn_alias import LearnAliasArgs, LearnAliasTool
from jarvis.tools.local.open_app import OpenAppArgs, OpenAppTool


def _first_match(utterance: str):
    best = None
    for vp in LearnAliasTool.voice_patterns:
        m = re.match(vp.regex, utterance)
        if m is None:
            continue
        if best is None or vp.priority < best[0]:
            best = (vp.priority, vp.args(m))
    return best


# --- voice patterns extract alias + target ------------------------------

@pytest.mark.parametrize(
    "utterance,alias,target",
    [
        ("запомни баттл это Battle.net", "баттл", "Battle.net"),
        ("запомни, что клаут код это Claude", "клаут код", "Claude"),
        ("запам'ятай баттл це Battle.net", "баттл", "Battle.net"),
        ("remember battl is Battle.net", "battl", "Battle.net"),
        ("remember that cloud code is Claude Code", "cloud code", "Claude Code"),
    ],
)
def test_patterns_extract_alias_and_target(utterance, alias, target):
    got = _first_match(utterance)
    assert got is not None, utterance
    assert got[1] == {"alias": alias, "target": target}


def test_plain_open_command_does_not_match():
    assert _first_match("открой баттл") is None
    assert _first_match("open chrome") is None


# --- execute records the alias ------------------------------------------

async def test_execute_learns_and_resolves(tmp_path):
    store = PersonalAliasStore(tmp_path / "aliases.json")
    tool = LearnAliasTool(personal_aliases=store)
    result = await tool.execute(LearnAliasArgs(alias="баттл", target="Battle.net"))
    assert result.success
    # Stored lowercased; resolution is case-insensitive.
    assert store.resolve("баттл") == "battle.net"


async def test_execute_empty_fields_fail(tmp_path):
    store = PersonalAliasStore(tmp_path / "aliases.json")
    tool = LearnAliasTool(personal_aliases=store)
    result = await tool.execute(LearnAliasArgs(alias="  ", target="Battle.net"))
    assert result.success is False


async def test_execute_without_store_fails():
    tool = LearnAliasTool(personal_aliases=None)
    result = await tool.execute(LearnAliasArgs(alias="баттл", target="Battle.net"))
    assert result.success is False
    assert "not available" in (result.error or "")


async def test_learned_alias_feeds_open_app_resolution(tmp_path):
    """The whole point: after learning, open_app's personal-alias lookup
    resolves the taught shortcut."""
    store = PersonalAliasStore(tmp_path / "aliases.json")
    await LearnAliasTool(personal_aliases=store).execute(
        LearnAliasArgs(alias="баттл", target="battlenet")
    )
    # open_app should now map "баттл" -> "battlenet" via the personal store
    # before any fuzzy/alias step. app_resolver=None so nothing real launches;
    # we just assert the token handed to the shell is the learned target.
    with patch("jarvis.tools.local.open_app.winplat.launch_app") as launch:
        result = await OpenAppTool(
            app_resolver=lambda q: None, personal_aliases=store
        ).execute(OpenAppArgs(name="баттл"))
    assert result.success
    launch.assert_called_once_with("battlenet")
