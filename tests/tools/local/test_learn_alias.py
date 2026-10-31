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
    resolves the taught shortcut.

    Uses a made-up target that is NEITHER a built-in alias key NOR a real
    app, so the assertion proves the personal-alias path alone — it does
    not depend on the built-in alias table."""
    store = PersonalAliasStore(tmp_path / "aliases.json")
    await LearnAliasTool(personal_aliases=store).execute(
        LearnAliasArgs(alias="баттл", target="myapp-for-test")
    )
    # open_app now maps "баттл" -> "myapp-for-test" via the personal store
    # before any fuzzy/alias step. app_resolver=None so nothing real
    # launches; we assert the token handed to the shell is the learned one.
    with patch("jarvis.tools.local.open_app.winplat.launch_app") as launch:
        result = await OpenAppTool(
            app_resolver=lambda q: None, personal_aliases=store
        ).execute(OpenAppArgs(name="баттл"))
    assert result.success
    launch.assert_called_once_with("myapp-for-test")


async def test_learned_alias_target_is_normalised_via_builtin_table(tmp_path):
    """Contract: a personal alias's TARGET is resolved like any ordinary
    spoken name — including through the built-in alias table. The priority
    of the personal alias KEY over built-in keys is preserved (the key
    "баттл" is looked up in the personal store first); only the resolved
    target is then normalised once via the built-in table.

    Here the user taught "баттл" -> "battlenet"; "battlenet" is itself a
    built-in alias for the canonical "Battle.net", so open_app launches
    "Battle.net" (Task 8c deliberate behaviour)."""
    store = PersonalAliasStore(tmp_path / "aliases.json")
    await LearnAliasTool(personal_aliases=store).execute(
        LearnAliasArgs(alias="баттл", target="battlenet")
    )
    with patch("jarvis.tools.local.open_app.winplat.launch_app") as launch:
        result = await OpenAppTool(
            app_resolver=lambda q: None, personal_aliases=store
        ).execute(OpenAppArgs(name="баттл"))
    assert result.success
    launch.assert_called_once_with("Battle.net")


async def test_personal_alias_resolution_is_single_pass_no_chaining(tmp_path):
    """Resolution is single-pass: personal alias -> target -> ONE built-in
    normalisation. A personal target that happens to equal ANOTHER personal
    alias KEY must NOT be followed (no personal->personal chaining), and a
    mutual pair ("а"->"б", "б"->"а") must not loop.

    Teach "foo"->"bar" and "bar"->"baz". Opening "foo" resolves to "bar"
    once and stops — it must NOT chain to "baz". Neither "bar" nor "baz" is
    a built-in alias, so the launch target is exactly "bar"."""
    store = PersonalAliasStore(tmp_path / "aliases.json")
    tool = LearnAliasTool(personal_aliases=store)
    await tool.execute(LearnAliasArgs(alias="foo", target="bar"))
    await tool.execute(LearnAliasArgs(alias="bar", target="baz"))
    with patch("jarvis.tools.local.open_app.winplat.launch_app") as launch:
        result = await OpenAppTool(
            app_resolver=lambda q: None, personal_aliases=store
        ).execute(OpenAppArgs(name="foo"))
    assert result.success
    # single hop: foo -> bar (NOT foo -> bar -> baz)
    launch.assert_called_once_with("bar")


async def test_personal_alias_mutual_pair_does_not_loop(tmp_path):
    """A mutual pair must terminate (single-pass guarantees it)."""
    store = PersonalAliasStore(tmp_path / "aliases.json")
    tool = LearnAliasTool(personal_aliases=store)
    await tool.execute(LearnAliasArgs(alias="a", target="b"))
    await tool.execute(LearnAliasArgs(alias="b", target="a"))
    with patch("jarvis.tools.local.open_app.winplat.launch_app") as launch:
        result = await OpenAppTool(
            app_resolver=lambda q: None, personal_aliases=store
        ).execute(OpenAppArgs(name="a"))
    assert result.success
    # "a" -> "b" and stops; never bounces back to "a".
    launch.assert_called_once_with("b")
