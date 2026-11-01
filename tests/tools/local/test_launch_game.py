"""Tests for jarvis.tools.local.launch_game.LaunchGameTool (Task 8c commit 4).

Unified game launch across Steam/Epic/GOG. The launcher index is injected,
and the platform launch seam is patched, so nothing real runs. Real launches
are verified at home ("проверить дома").
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.platform.game_launchers import Game, Launcher, LaunchKind, LaunchSpec
from jarvis.tools.local.launch_game import LaunchGameArgs, LaunchGameTool


def _uri_game(name: str, launcher: Launcher, uri: str = "x://y") -> Game:
    return Game(
        name=name,
        launcher=launcher,
        launch_spec=LaunchSpec(kind=LaunchKind.URI, uri=uri),
    )


def _index(*games: Game) -> dict[str, Game]:
    from jarvis.platform.app_discovery import normalize_query

    return {normalize_query(g.name): g for g in games}


_SPEC_SEAM = "jarvis.platform.game_launchers.launch_game_spec"


async def test_launches_resolved_game_and_announces_launcher():
    idx = _index(_uri_game("Elden Ring", Launcher.EPIC))
    tool = LaunchGameTool(index_provider=lambda: idx)
    with patch(_SPEC_SEAM) as spec:
        result = await tool.execute(LaunchGameArgs(game_name="elden ring"))
    assert result.success
    assert "Elden Ring" in (result.output or "")
    assert "Epic" in (result.output or "")  # announces the launcher
    spec.assert_called_once()


async def test_fuzzy_resolves_close_title():
    idx = _index(_uri_game("The Witcher 3", Launcher.GOG))
    tool = LaunchGameTool(index_provider=lambda: idx)
    with patch(_SPEC_SEAM):
        result = await tool.execute(LaunchGameArgs(game_name="witcher 3"))
    assert result.success
    assert "GOG" in (result.output or "")


async def test_near_miss_sets_suggestion_and_does_not_launch():
    """A title that only loosely matches must NOT launch; it returns a
    suggestion for a "did you mean?" prompt (condition 3)."""
    # "doom slayer" shares one token with "Doom Eternal" but is neither a
    # prefix nor a substring, so it lands in the near-miss band
    # (floor <= score < cutoff), not a confident hit.
    idx = _index(_uri_game("Doom Eternal", Launcher.STEAM))
    tool = LaunchGameTool(index_provider=lambda: idx)
    with patch(_SPEC_SEAM) as spec:
        result = await tool.execute(LaunchGameArgs(game_name="doom slayer"))
    assert result.success is False
    spec.assert_not_called()
    assert result.suggestion == "Doom Eternal"


async def test_no_match_returns_not_found_without_launch():
    idx = _index(_uri_game("Hades", Launcher.STEAM))
    tool = LaunchGameTool(index_provider=lambda: idx)
    with patch(_SPEC_SEAM) as spec:
        result = await tool.execute(LaunchGameArgs(game_name="zzzznotagame"))
    assert result.success is False
    assert result.suggestion is None
    spec.assert_not_called()


async def test_multi_launcher_priority_is_respected():
    """Same title in two launchers resolves to the one the index kept
    (index assembly order = launcher priority). Here the index already
    holds the Steam copy (first-writer-wins upstream)."""
    steam_portal = _uri_game("Portal", Launcher.STEAM, uri="steam://run/400")
    idx = _index(steam_portal)  # Steam won during indexing
    tool = LaunchGameTool(index_provider=lambda: idx)
    with patch(_SPEC_SEAM):
        result = await tool.execute(LaunchGameArgs(game_name="portal"))
    assert result.success
    assert "Steam" in (result.output or "")


async def test_empty_name_rejected():
    tool = LaunchGameTool(index_provider=lambda: {})
    with patch(_SPEC_SEAM) as spec:
        result = await tool.execute(LaunchGameArgs(game_name="   "))
    assert result.success is False
    spec.assert_not_called()


# --- condition 4: index readiness / failure resilience ------------------


async def test_index_still_loading_says_loading_not_not_found():
    """If the index is not ready within the timeout, say it's loading —
    never a misleading 'game not found'."""
    tool = LaunchGameTool(
        index_provider=lambda: None,  # never ready
        index_wait_timeout_s=0.05,
    )
    with patch(_SPEC_SEAM) as spec:
        result = await tool.execute(LaunchGameArgs(game_name="elden ring"))
    assert result.success is False
    assert "loading" in (result.error or "").lower()
    spec.assert_not_called()


async def test_index_build_failure_falls_back_to_steam():
    """If the unified index build fails, the legacy Steam path is used so
    the user is never left without their Steam games (condition 4)."""
    class _SteamGame:
        name = "Portal 2"
        app_id = 620

    # No index_provider => synchronous build; make build_game_index raise.
    def _boom(*_a, **_k):
        raise RuntimeError("index build exploded")

    with (
        patch("jarvis.platform.game_launchers.build_game_index", side_effect=_boom),
        patch("jarvis.platform.windows_apps.resolve_steam_game", return_value=_SteamGame()),
        patch("jarvis.platform.windows.launch_steam_game") as launch_steam,
    ):
        tool = LaunchGameTool()  # index_provider=None -> builds, which raises
        result = await tool.execute(LaunchGameArgs(game_name="portal 2"))
    assert result.success
    assert "Steam" in (result.output or "")
    launch_steam.assert_called_once_with(620)


async def test_index_build_failure_no_steam_match_reports_not_found():
    def _boom(*_a, **_k):
        raise RuntimeError("index build exploded")

    with (
        patch("jarvis.platform.game_launchers.build_game_index", side_effect=_boom),
        patch("jarvis.platform.windows_apps.resolve_steam_game", return_value=None),
    ):
        tool = LaunchGameTool()
        result = await tool.execute(LaunchGameArgs(game_name="nonexistent"))
    assert result.success is False
    assert "nonexistent" in (result.error or "")
