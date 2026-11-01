"""Launch an installed Steam library game by title."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from jarvis.platform import windows as winplat
from jarvis.tools.registry import ToolResult, VoicePattern

if TYPE_CHECKING:
    from jarvis.platform.windows_apps import SteamGame


class LaunchSteamGameArgs(BaseModel):
    game_name: str = Field(
        description=(
            "Title of the Steam game to launch, as the user said it "
            "(e.g. 'Elden Ring', 'Portal 2')."
        ),
    )


class LaunchSteamGameTool:
    name: str = "launch_steam_game"
    description: str = (
        "Legacy: launch a game from the user's STEAM library only. Use "
        "ONLY when the user explicitly says Steam — e.g. 'launch Cyberpunk "
        "on Steam', 'start Portal in Steam'. For any other game request "
        "(no store named, or Epic/GOG) prefer launch_game, which searches "
        "every installed launcher. Do NOT use for music, songs, YouTube, "
        "or opening the Steam client itself (use open_app). "
        "(RU: запусти ... в стиме / UK: запусти ... в стімі)"
    )
    args_schema = LaunchSteamGameArgs
    requires_confirmation: bool = False

    # Voice patterns for deterministic dispatch — skip LLM for Steam games.
    #
    # Priority 105: below play_youtube_music (100) so "play pink floyd" is
    # music, but above open_app (PRIORITY_CATCH_ALL) so "launch X on steam"
    # is a game, not an app launch.
    # The generic "игру / гру / the game" phrases moved to LaunchGameTool
    # (Task 8c commit 4); only the explicit "... on steam / ... в стиме"
    # phrases remain here.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "launch elden ring on steam", "start portal in steam"
        VoicePattern(
            regex=r"^(?:launch|start|boot(?:\s+up)?|run)\s+(.+?)\s+(?:on|in|from)\s+steam$",
            priority=105,
            args=lambda m: {"game_name": m.group(1)},
        ),
        # EN: "play cyberpunk on steam"
        VoicePattern(
            regex=r"^play\s+(.+?)\s+(?:on|in)\s+steam$",
            priority=105,
            args=lambda m: {"game_name": m.group(1)},
        ),
        # RU: "запусти сталкер в стиме", "включи киберпанк из стима"
        VoicePattern(
            regex=r"^(?:запусти|включи|загрузи)\s+(.+?)\s+(?:в|из|на)\s+стим[еуа]?$",
            priority=105,
            args=lambda m: {"game_name": m.group(1)},
        ),
        # UK: "запусти сталкер в стімі", "увімкни кіберпанк із стіму"
        VoicePattern(
            regex=r"^(?:запусти|увімкни|завантаж)\s+(.+?)\s+(?:в|із)\s+стім[іу]?$",
            priority=105,
            args=lambda m: {"game_name": m.group(1)},
        ),
    )

    def __init__(
        self,
        *,
        steam_resolver: Callable[[str], SteamGame | None] | None = None,
    ) -> None:
        self._steam_resolver = steam_resolver

    def _resolve(self, query: str) -> SteamGame | None:
        if self._steam_resolver is not None:
            return self._steam_resolver(query)
        from jarvis.platform.windows_apps import resolve_steam_game

        return resolve_steam_game(query)

    async def execute(self, args: LaunchSteamGameArgs) -> ToolResult:
        name = args.game_name.strip()
        if not name:
            return ToolResult(success=False, error="game name is empty")

        game = await asyncio.to_thread(self._resolve, name)
        if game is None:
            return ToolResult(
                success=False,
                error=(
                    f"no installed Steam game matched {name!r}. "
                    "Check the title or install the game in Steam."
                ),
            )
        try:
            await asyncio.to_thread(winplat.launch_steam_game, game.app_id)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except OSError as e:
            return ToolResult(
                success=False,
                error=f"could not launch {game.name!r}: {e}",
            )
        return ToolResult(
            success=True,
            output=f"Launching {game.name} on Steam, sir.",
        )
