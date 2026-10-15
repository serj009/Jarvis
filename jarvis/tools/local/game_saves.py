"""Game save locator and backup tool.

Knows standard save locations for 10 popular games.
Can find and backup save directories.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known game save paths
# ---------------------------------------------------------------------------

# Environment variable tokens used in path templates.
def _expand(path_template: str) -> Path:
    """Expand {APPDATA}, {LOCALAPPDATA}, {USERPROFILE} tokens."""
    replacements = {
        "{APPDATA}": os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")),
        "{LOCALAPPDATA}": os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
        "{USERPROFILE}": os.environ.get("USERPROFILE", str(Path.home())),
    }
    result = path_template
    for token, value in replacements.items():
        result = result.replace(token, value)
    return Path(result)


KNOWN_GAMES: dict[str, dict] = {
    "dark_souls_2": {
        "aliases": ["dark souls 2", "ds2", "dark souls ii", "дарк соулс 2"],
        "paths": ["{APPDATA}/DarkSoulsII"],
        "display_name": "Dark Souls II",
    },
    "dark_souls_3": {
        "aliases": ["dark souls 3", "ds3", "dark souls iii", "дарк соулс 3"],
        "paths": ["{APPDATA}/DarkSoulsIII"],
        "display_name": "Dark Souls III",
    },
    "elden_ring": {
        "aliases": ["elden ring", "елден рінг", "элден ринг"],
        "paths": ["{APPDATA}/EldenRing"],
        "display_name": "Elden Ring",
    },
    "stalker_2": {
        "aliases": ["stalker 2", "сталкер 2", "stalker2", "s.t.a.l.k.e.r. 2"],
        "paths": [
            "{LOCALAPPDATA}/Stalker2/Saved/SaveGames",
            "{USERPROFILE}/Saved Games/Stalker2",
        ],
        "display_name": "S.T.A.L.K.E.R. 2",
    },
    "cyberpunk_2077": {
        "aliases": ["cyberpunk", "cyberpunk 2077", "кіберпанк", "киберпанк"],
        "paths": ["{USERPROFILE}/Saved Games/CD Projekt Red/Cyberpunk 2077"],
        "display_name": "Cyberpunk 2077",
    },
    "skyrim": {
        "aliases": ["skyrim", "скайрім", "скайрим", "skyrim se"],
        "paths": ["{USERPROFILE}/Documents/My Games/Skyrim Special Edition/Saves"],
        "display_name": "Skyrim Special Edition",
    },
    "witcher_3": {
        "aliases": ["witcher 3", "відьмак 3", "ведьмак 3", "witcher3", "the witcher 3"],
        "paths": ["{USERPROFILE}/Documents/The Witcher 3/gamesaves"],
        "display_name": "The Witcher 3",
    },
    "minecraft": {
        "aliases": ["minecraft", "майнкрафт"],
        "paths": ["{APPDATA}/.minecraft/saves"],
        "display_name": "Minecraft",
    },
    "baldurs_gate_3": {
        "aliases": ["baldurs gate 3", "bg3", "балдурс гейт 3", "baldur's gate 3"],
        "paths": ["{LOCALAPPDATA}/Larian Studios/Baldur's Gate 3/PlayerProfiles"],
        "display_name": "Baldur's Gate 3",
    },
    "gta_5": {
        "aliases": ["gta 5", "gta v", "гта 5", "grand theft auto 5"],
        "paths": [
            "{USERPROFILE}/Documents/Rockstar Games/GTA V/Profiles",
            "{LOCALAPPDATA}/Rockstar Games/GTA V",
        ],
        "display_name": "GTA V",
    },
}


def _resolve_game(query: str) -> tuple[str, dict] | None:
    """Find a game by name/alias (case-insensitive)."""
    q = query.lower().strip()
    for key, info in KNOWN_GAMES.items():
        if q == key:
            return key, info
        for alias in info["aliases"]:
            if q == alias.lower():
                return key, info
    # Fuzzy substring match as fallback.
    for key, info in KNOWN_GAMES.items():
        for alias in info["aliases"]:
            if q in alias.lower() or alias.lower() in q:
                return key, info
    return None


def _find_save_path(game_info: dict) -> Path | None:
    """Find the first existing save directory for a game."""
    for tmpl in game_info["paths"]:
        path = _expand(tmpl)
        if path.exists():
            return path
    return None


def _backup_saves(src: Path, backup_dir: Path, game_name: str) -> dict:
    """Copy save directory to backup location with timestamp."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{game_name}_{timestamp}"
    try:
        shutil.copytree(src, dest)
        # Count files.
        file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
        total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
        return {
            "success": True,
            "backup_path": str(dest),
            "file_count": file_count,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class FindSavesArgs(BaseModel):
    game: str = Field(description="Game name to find saves for.")


class BackupSavesArgs(BaseModel):
    game: str = Field(description="Game name to backup saves for.")
    backup_dir: str = Field(
        default="",
        description="Backup destination directory. Defaults to ~/Documents/GameBackups.",
    )


class ListKnownGamesArgs(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

class FindGameSavesTool:
    name: str = "find_game_saves"
    description: str = (
        "Finds the save file location for a known game. Supports: "
        "Dark Souls 2/3, Elden Ring, Stalker 2, Cyberpunk, Skyrim, "
        "Witcher 3, Minecraft, Baldur's Gate 3, GTA V. "
        "(RU: где сохранения, найди сохранения / UK: де збереження, знайди збереження)"
    )
    args_schema = FindSavesArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:find|where\s+are)\s+(?:the\s+)?saves?\s+(?:for\s+)?(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:где|найди)\s+сохранени[яе]\s+(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:де|знайди)\s+збереженн[яі]\s+(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
    )

    async def execute(self, args: FindSavesArgs) -> ToolResult:
        match = _resolve_game(args.game)
        if match is None:
            known = ", ".join(g["display_name"] for g in KNOWN_GAMES.values())
            return ToolResult(
                success=False,
                error=f"Unknown game '{args.game}'. Known games: {known}",
            )

        key, info = match
        try:
            path = await asyncio.to_thread(_find_save_path, info)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        display = info["display_name"]
        if path is None:
            expected = ", ".join(info["paths"])
            return ToolResult(
                success=True,
                output=(
                    f"Save directory for {display} not found. "
                    f"Expected locations: {expected}"
                ),
            )
        return ToolResult(
            success=True,
            output=f"{display} saves found at: {path}",
        )


class BackupGameSavesTool:
    name: str = "backup_game_saves"
    description: str = (
        "Backs up game save files to a specified directory (default ~/Documents/GameBackups). "
        "Creates a timestamped copy. "
        "(RU: бэкап сохранений, сделай бэкап / UK: бекап збережень, зроби бекап)"
    )
    args_schema = BackupSavesArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^backup\s+(?:saves?\s+)?(?:for\s+)?(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:сделай\s+)?бэкап\s+(?:сохранений\s+)?(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:зроби\s+)?бекап\s+(?:збережень\s+)?(.+)$",
            priority=200,
            args=lambda m: {"game": m.group(1)},
        ),
    )

    async def execute(self, args: BackupSavesArgs) -> ToolResult:
        match = _resolve_game(args.game)
        if match is None:
            return ToolResult(success=False, error=f"Unknown game '{args.game}'.")

        key, info = match
        save_path = await asyncio.to_thread(_find_save_path, info)
        if save_path is None:
            return ToolResult(
                success=False,
                error=f"Save directory for {info['display_name']} not found.",
            )

        backup_dir = (
            Path(args.backup_dir)
            if args.backup_dir
            else Path.home() / "Documents" / "GameBackups"
        )

        try:
            result = await asyncio.to_thread(
                _backup_saves, save_path, backup_dir, key,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Backup failed: {e}")

        if result.get("success"):
            return ToolResult(
                success=True,
                output=(
                    f"Backed up {info['display_name']} saves: "
                    f"{result['file_count']} files ({result['total_size_mb']} MB) "
                    f"to {result['backup_path']}, sir."
                ),
            )
        return ToolResult(success=False, error=result.get("error", "Backup failed."))


class ListKnownGamesTool:
    name: str = "list_known_games"
    description: str = (
        "Lists all games whose save locations are known. "
        "(RU: какие игры знаешь, список игр / UK: які ігри знаєш, список ігор)"
    )
    args_schema = ListKnownGamesArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^list\s+(?:known\s+)?games$", priority=200),
        VoicePattern(regex=r"^(?:какие|список)\s+(?:игр|игры)$", priority=200),
        VoicePattern(regex=r"^(?:які|список)\s+(?:ігор|ігри)$", priority=200),
    )

    async def execute(self, args: ListKnownGamesArgs) -> ToolResult:
        lines = ["Known games with save locations:"]
        for info in KNOWN_GAMES.values():
            lines.append(f"  • {info['display_name']}")
        return ToolResult(success=True, output="\n".join(lines))
