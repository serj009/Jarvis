"""Tests for jarvis.tools.local.game_saves.

All tests mock filesystem operations (shutil.copytree, Path.exists).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from jarvis.tools.local.game_saves import (
    BackupGameSavesTool,
    BackupSavesArgs,
    FindGameSavesTool,
    FindSavesArgs,
    ListKnownGamesTool,
    _resolve_game,
)
from jarvis.tools.registry import EmptyArgs

# --- Game resolution unit tests -------------------------------------------

def test_resolve_game_exact():
    result = _resolve_game("elden ring")
    assert result is not None
    key, info = result
    assert key == "elden_ring"


def test_resolve_game_alias():
    result = _resolve_game("ds3")
    assert result is not None
    key, info = result
    assert key == "dark_souls_3"


def test_resolve_game_cyrillic():
    result = _resolve_game("кіберпанк")
    assert result is not None
    key, info = result
    assert key == "cyberpunk_2077"


def test_resolve_game_unknown():
    assert _resolve_game("totally unknown game") is None


def test_resolve_game_bg3():
    result = _resolve_game("bg3")
    assert result is not None
    assert result[0] == "baldurs_gate_3"


# --- FindGameSavesTool ----------------------------------------------------

async def test_find_saves_found():
    with patch("jarvis.tools.local.game_saves._find_save_path") as mock_find:
        mock_find.return_value = Path("C:/Users/test/AppData/Roaming/EldenRing")
        result = await FindGameSavesTool().execute(
            FindSavesArgs(game="elden ring"),
        )
    assert result.success
    assert "Elden Ring" in (result.output or "")
    assert "EldenRing" in (result.output or "")


async def test_find_saves_not_found():
    with patch("jarvis.tools.local.game_saves._find_save_path", return_value=None):
        result = await FindGameSavesTool().execute(
            FindSavesArgs(game="elden ring"),
        )
    assert result.success  # Not an error — just not found
    assert "not found" in (result.output or "").lower()


async def test_find_saves_unknown_game():
    result = await FindGameSavesTool().execute(
        FindSavesArgs(game="tetris"),
    )
    assert not result.success
    assert "Unknown" in (result.error or "")


# --- BackupGameSavesTool --------------------------------------------------

async def test_backup_success():
    with patch("jarvis.tools.local.game_saves._find_save_path") as mock_find, \
         patch("jarvis.tools.local.game_saves._backup_saves") as mock_backup:
        mock_find.return_value = Path("C:/saves/EldenRing")
        mock_backup.return_value = {
            "success": True,
            "backup_path": "C:/backups/elden_ring_20261015",
            "file_count": 5,
            "total_size_mb": 12.3,
        }
        result = await BackupGameSavesTool().execute(
            BackupSavesArgs(game="elden ring"),
        )
    assert result.success
    assert "5 files" in (result.output or "")
    assert "12.3 MB" in (result.output or "")


async def test_backup_save_not_found():
    with patch("jarvis.tools.local.game_saves._find_save_path", return_value=None):
        result = await BackupGameSavesTool().execute(
            BackupSavesArgs(game="elden ring"),
        )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_backup_unknown_game():
    result = await BackupGameSavesTool().execute(
        BackupSavesArgs(game="pong"),
    )
    assert not result.success


# --- ListKnownGamesTool ---------------------------------------------------

async def test_list_known_games():
    result = await ListKnownGamesTool().execute(EmptyArgs())
    assert result.success
    assert "Elden Ring" in (result.output or "")
    assert "Dark Souls" in (result.output or "")
    assert "Minecraft" in (result.output or "")


def test_requires_confirmation_false():
    assert FindGameSavesTool().requires_confirmation is False
    assert BackupGameSavesTool().requires_confirmation is False
    assert ListKnownGamesTool().requires_confirmation is False
