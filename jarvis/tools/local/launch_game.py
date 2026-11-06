"""Launch an installed game from ANY launcher (Steam, Epic, GOG).

Task 8c commit 4. This is the unified entry point the model should prefer
for game launches; launch_steam_game remains for the explicit "on Steam"
phrasing (legacy, Steam-only).

Resolution
----------
A background-built index maps normalized title -> Game across launchers
(SteamGamesSource, EpicGamesSource, GogGamesSource), assembled in
``games.launcher_priority`` order so a title installed in several launchers
resolves to the highest-priority one (first-writer-wins in index_games).

* Exact/fuzzy hit (score >= cutoff): launch via the game's LaunchSpec
  (protocol URI or a contained .exe — never a shell), and announce the
  launcher ("Launching X via Epic, sir.").
* Near-miss (floor <= score < cutoff): NEVER launch; return a
  ToolResult.suggestion so the router can ask "did you mean Y?".
* Index not ready yet: wait up to a short timeout; if still not ready,
  say the list is still loading (NOT "game not found").
* Index build failed: fall back to the existing Steam-only path (logged
  WARNING) so the user is never left without their Steam games.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from jarvis.platform.app_discovery import fuzzy_resolve, fuzzy_suggest
from jarvis.tools.registry import ToolResult, VoicePattern

if TYPE_CHECKING:
    from jarvis.platform.game_launchers import Game
    from jarvis.platform.windows_apps import SteamGame

log = logging.getLogger(__name__)

# Fuzzy thresholds (mirror open_app semantics).
_MATCH_CUTOFF = 0.55
_SUGGEST_FLOOR = 0.35

# How long launch_game waits for a background index build before giving up
# with a "still loading" message rather than a false "not found".
_INDEX_WAIT_TIMEOUT_S = 3.0

# Human-facing launcher names for the spoken confirmation.
_LAUNCHER_LABEL = {
    "steam": "Steam",
    "epic": "Epic",
    "gog": "GOG",
    "battlenet": "Battle.net",
}


class LaunchGameArgs(BaseModel):
    game_name: str = Field(
        description=(
            "Title of the game to launch, as the user said it "
            "(e.g. 'Elden Ring', 'The Witcher 3')."
        ),
    )


class LaunchGameTool:
    name: str = "launch_game"
    description: str = (
        "Launch an installed game from ANY launcher (Steam, Epic, GOG). "
        "Prefer THIS for any request to play/launch/start/boot a game when "
        "the user does not name a specific store — e.g. 'play Elden Ring', "
        "'launch the game Control', 'запусти игру Ведьмак'. Do NOT use for "
        "music, songs, YouTube, or opening a launcher application itself "
        "(use open_app for that). "
        "(RU: запусти игру / UK: запусти гру)"
    )
    args_schema = LaunchGameArgs
    requires_confirmation: bool = False
    ends_turn_on_success: bool = True

    # Moved here from LaunchSteamGameTool (Task 8c commit 4): the generic
    # "игру / гру / the game" phrases now search ALL launchers. The explicit
    # "... on steam / ... в стиме" phrases stay on launch_steam_game.
    # Priority 108 preserved so these keep beating the open_app catch-all
    # (900) and never collide with the steam-specific patterns (105).
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # RU: "запусти игру ведьмак", "включи игру портал"
        VoicePattern(
            regex=r"^(?:запусти|включи)\s+игру\s+(.+)$",
            priority=108,
            args=lambda m: {"game_name": m.group(1)},
        ),
        # UK: "запусти гру сталкер", "увімкни гру портал"
        VoicePattern(
            regex=r"^(?:запусти|увімкни)\s+гру\s+(.+)$",
            priority=108,
            args=lambda m: {"game_name": m.group(1)},
        ),
        # EN: "launch the game control", "play the game hades",
        # "start the game portal" — NEW pattern (no prior EN game-word
        # pattern existed; the Steam tool only had "... on steam").
        VoicePattern(
            regex=r"^(?:launch|play|start|run|boot(?:\s+up)?)\s+the\s+game\s+(.+)$",
            priority=108,
            args=lambda m: {"game_name": m.group(1)},
        ),
    )

    def __init__(
        self,
        *,
        index_provider: Callable[[], dict[str, Game] | None] | None = None,
        steam_resolver: Callable[[str], SteamGame | None] | None = None,
        launcher_priority: list[str] | None = None,
        match_cutoff: float = _MATCH_CUTOFF,
        suggest_floor: float = _SUGGEST_FLOOR,
        index_wait_timeout_s: float = _INDEX_WAIT_TIMEOUT_S,
    ) -> None:
        # index_provider returns the current game index, None if still
        # building. Injectable for tests; default builds synchronously.
        self._index_provider = index_provider
        # Steam-only fallback used if the unified index fails to build.
        self._steam_resolver = steam_resolver
        self._launcher_priority = launcher_priority
        self._match_cutoff = match_cutoff
        self._suggest_floor = suggest_floor
        self._index_wait_timeout_s = index_wait_timeout_s

    # -- index access -----------------------------------------------------

    def _build_index(self) -> dict[str, Game]:
        from jarvis.platform.game_launchers import build_game_index

        return build_game_index(self._launcher_priority)

    async def _get_index(self) -> tuple[dict[str, Game] | None, str]:
        """Return (index, status). status is "ready", "loading", or
        "failed". Waits briefly for a provider that is still building."""
        if self._index_provider is None:
            # No background provider: build synchronously off the hot path.
            try:
                idx = await asyncio.to_thread(self._build_index)
                return idx, "ready"
            except Exception:
                log.warning("launch_game: index build failed", exc_info=True)
                return None, "failed"

        deadline = asyncio.get_running_loop().time() + self._index_wait_timeout_s
        while True:
            try:
                idx = self._index_provider()
            except Exception:
                log.warning("launch_game: index provider raised", exc_info=True)
                return None, "failed"
            if idx is not None:
                return idx, "ready"
            if asyncio.get_running_loop().time() >= deadline:
                return None, "loading"
            await asyncio.sleep(0.05)

    # -- steam-only fallback ---------------------------------------------

    async def _steam_fallback(self, name: str) -> ToolResult | None:
        """Resolve + launch via the legacy Steam-only path. Returns a
        ToolResult on a definitive outcome, or None if no Steam match so
        the caller can still offer a near-miss suggestion."""
        from jarvis.platform import windows as winplat

        def _resolve() -> SteamGame | None:
            if self._steam_resolver is not None:
                return self._steam_resolver(name)
            from jarvis.platform.windows_apps import resolve_steam_game

            return resolve_steam_game(name)

        try:
            game = await asyncio.to_thread(_resolve)
        except Exception:
            log.warning("launch_game: steam fallback resolve failed", exc_info=True)
            return None
        if game is None:
            return None
        try:
            await asyncio.to_thread(winplat.launch_steam_game, game.app_id)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except OSError as e:
            return ToolResult(success=False, error=f"could not launch {game.name!r}: {e}")
        return ToolResult(success=True, output=f"Launching {game.name} via Steam, sir.")

    # -- execute ----------------------------------------------------------

    async def execute(self, args: LaunchGameArgs) -> ToolResult:
        name = args.game_name.strip()
        if not name:
            return ToolResult(success=False, error="game name is empty")

        index, status = await self._get_index()

        if status == "failed" or index is None:
            # Never leave the user without Steam games: try the legacy path.
            if status == "failed":
                fallback = await self._steam_fallback(name)
                if fallback is not None:
                    return fallback
                return ToolResult(
                    success=False,
                    error=f"no installed game matched {name!r}.",
                )
            # status == "loading"
            return ToolResult(
                success=False,
                error="the game list is still loading — try again in a moment.",
            )

        hit = fuzzy_resolve(name, index, cutoff=self._match_cutoff)
        if hit is not None:
            game = hit[1]
            from jarvis.platform.game_launchers import launch_game_spec

            label = _LAUNCHER_LABEL.get(str(game.launcher), str(game.launcher))
            try:
                await asyncio.to_thread(launch_game_spec, game.launch_spec)
            except NotImplementedError as e:
                return ToolResult(success=False, error=str(e))
            except (OSError, ValueError) as e:
                return ToolResult(
                    success=False,
                    error=f"could not launch {game.name!r} via {label}: {e}",
                )
            return ToolResult(
                success=True,
                output=f"Launching {game.name} via {label}, sir.",
            )

        # No confident hit: offer a near-miss (never launch it).
        near = fuzzy_suggest(
            name, index, floor=self._suggest_floor, cutoff=self._match_cutoff
        )
        if near is not None:
            suggested = near[1]
            return ToolResult(
                success=False,
                error=f"no installed game matched {name!r}.",
                suggestion=suggested.name,
            )
        return ToolResult(
            success=False,
            error=f"no installed game matched {name!r}.",
        )
