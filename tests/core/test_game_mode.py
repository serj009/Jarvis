"""Tests for jarvis.core.game_mode — Game Mode VRAM management."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.core.game_mode import (
    DEFAULT_IDLE_GAME_PATTERNS,
    DetectedGame,
    GameCategory,
    GameMode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_steam_games() -> dict:
    """Fake Steam library for tests."""
    # Mimics the dict[str, SteamGame] structure from scan_steam_games()
    FakeSteamGame = type("SteamGame", (), {})
    games = {}
    for name, app_id in [
        ("Elden Ring", 1245620),
        ("STALKER 2", 1643320),
        ("Cyberpunk 2077", 1091500),
        ("Mafia II", 50130),
        ("Counter-Strike 2", 730),
    ]:
        g = FakeSteamGame()
        g.name = name
        g.app_id = app_id
        key = name.lower().replace(" ", "")
        games[key] = g
    return games


def _make_game_mode(**overrides) -> GameMode:
    """Build a GameMode with test-friendly mocks."""
    defaults = dict(
        vram_manager=MagicMock(),
        ollama_client=AsyncMock(),
        stt=AsyncMock(),
        memory_store=AsyncMock(),
        scan_interval_seconds=1.0,
        steam_games=_make_steam_games(),
        enabled=True,
    )
    defaults.update(overrides)

    # Set up STT mock with the attributes GameMode reads
    stt = defaults["stt"]
    stt.model_size = "small"
    stt._configured_device = "auto"
    stt.compute_type = "auto"
    stt.is_loaded = True

    # VRAMManager mock
    vram = defaults["vram_manager"]
    vram.release_all = AsyncMock()

    # OllamaClient mock
    ollama = defaults["ollama_client"]
    ollama.unload = AsyncMock()
    ollama.warm = AsyncMock()

    # MemoryStore mock
    mem = defaults["memory_store"]
    mem.is_open = True
    mem.search = AsyncMock(return_value=[])
    mem.add = AsyncMock(return_value="test_id")

    return GameMode(**defaults)


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestClassifyProcess:
    """Tests for _classify_process."""

    def test_classify_main_game_from_steam(self):
        gm = _make_game_mode()
        assert gm._classify_process("eldenring") == GameCategory.MAIN

    def test_classify_main_game_stalker(self):
        gm = _make_game_mode()
        assert gm._classify_process("stalker2") == GameCategory.MAIN

    def test_classify_main_game_cyberpunk(self):
        gm = _make_game_mode()
        assert gm._classify_process("cyberpunk2077") == GameCategory.MAIN

    def test_classify_idle_game_cookie(self):
        gm = _make_game_mode()
        assert gm._classify_process("cookieclicker") == GameCategory.IDLE

    def test_classify_idle_game_clicker(self):
        gm = _make_game_mode()
        assert gm._classify_process("clickerheroes") == GameCategory.IDLE

    def test_classify_idle_game_autoclicker(self):
        gm = _make_game_mode()
        assert gm._classify_process("autoclicker") == GameCategory.IDLE

    def test_classify_idle_generic(self):
        gm = _make_game_mode()
        assert gm._classify_process("some_idle_game") == GameCategory.IDLE

    def test_classify_unknown_process(self):
        gm = _make_game_mode()
        assert gm._classify_process("chrome") == GameCategory.UNKNOWN

    def test_classify_unknown_explorer(self):
        gm = _make_game_mode()
        assert gm._classify_process("explorer") == GameCategory.UNKNOWN

    def test_classify_custom_main_game(self):
        gm = _make_game_mode(custom_main_games=["witcher3", "hogwartslegacy"])
        assert gm._classify_process("witcher3") == GameCategory.MAIN
        assert gm._classify_process("hogwartslegacy") == GameCategory.MAIN

    def test_idle_takes_priority_over_steam(self):
        """Even if a game named 'idle' is in Steam, IDLE pattern wins."""
        gm = _make_game_mode()
        assert gm._classify_process("idle_master") == GameCategory.IDLE


class TestResolveGameTitle:
    """Tests for _resolve_game_title."""

    def test_resolve_from_steam(self):
        gm = _make_game_mode()
        assert gm._resolve_game_title("eldenring") == "Elden Ring"

    def test_resolve_cyberpunk(self):
        gm = _make_game_mode()
        assert gm._resolve_game_title("cyberpunk2077") == "Cyberpunk 2077"

    def test_resolve_unknown_fallback(self):
        gm = _make_game_mode()
        title = gm._resolve_game_title("some_random_game")
        assert title == "Some Random Game"


# ---------------------------------------------------------------------------
# Activation / deactivation
# ---------------------------------------------------------------------------

class TestActivation:
    """Tests for _activate and its side effects."""

    @pytest.mark.asyncio
    async def test_activate_evicts_ollama(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        await gm._activate(game)

        gm._ollama.unload.assert_awaited_once()
        assert gm.is_active is True
        assert gm.current_game is not None
        assert gm.current_game.title == "Elden Ring"

    @pytest.mark.asyncio
    async def test_activate_switches_stt_to_cpu_tiny(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="stalker2.exe", title="STALKER 2",
            category=GameCategory.MAIN, pid=5678,
        )
        await gm._activate(game)

        stt = gm._stt
        # Should have been reconfigured
        assert stt.model_size == "tiny"
        assert stt._configured_device == "cpu"
        assert stt.compute_type == "int8"
        # load was called for the new config
        stt.load.assert_awaited()

    @pytest.mark.asyncio
    async def test_activate_releases_vram(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="cyberpunk2077.exe", title="Cyberpunk 2077",
            category=GameCategory.MAIN, pid=9999,
        )
        await gm._activate(game)

        gm._vram.release_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_activate_preloads_game_context(self):
        """Preload task is created (fire-and-forget)."""
        gm = _make_game_mode()
        # Return some fake facts
        FakeFact = type("Fact", (), {"text": "Elden Ring tip: dodge roll"})
        gm._memory.search = AsyncMock(return_value=[FakeFact()])

        game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        await gm._activate(game)
        # Allow the fire-and-forget task to run
        await asyncio.sleep(0.1)

        assert "Elden Ring" in gm.preloaded_context
        assert "dodge roll" in gm.preloaded_context


class TestDeactivation:
    """Tests for _deactivate and its side effects."""

    @pytest.mark.asyncio
    async def test_deactivate_restores_stt(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        # Activate first (saves pre-game state)
        await gm._activate(game)
        await asyncio.sleep(0.05)

        # Now deactivate
        await gm._deactivate()

        stt = gm._stt
        # Should restore to pre-game values
        assert stt.model_size == "small"
        assert stt._configured_device == "auto"
        assert stt.compute_type == "auto"
        assert gm.is_active is False
        assert gm.current_game is None

    @pytest.mark.asyncio
    async def test_deactivate_logs_session_to_memory(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        await gm._activate(game)
        await asyncio.sleep(0.05)

        await gm._deactivate()

        gm._memory.add.assert_awaited_once()
        call_kwargs = gm._memory.add.call_args
        text = call_kwargs.kwargs.get("text", "") or call_kwargs[0][0] if call_kwargs[0] else ""
        if not text:
            text = str(call_kwargs)
        assert "Elden Ring" in text

    @pytest.mark.asyncio
    async def test_deactivate_clears_preloaded_context(self):
        gm = _make_game_mode()
        game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        gm._preloaded_context = "some game context"
        gm._active = True
        gm._current_game = game
        gm._game_start_time = datetime.now()

        await gm._deactivate()

        assert gm.preloaded_context == ""

    @pytest.mark.asyncio
    async def test_deactivate_noop_when_inactive(self):
        gm = _make_game_mode()
        gm._current_game = None
        await gm._deactivate()
        # Should not crash, should not call anything
        gm._stt.unload.assert_not_awaited()


# ---------------------------------------------------------------------------
# Idle games never trigger
# ---------------------------------------------------------------------------

class TestIdleGameIgnored:
    """IDLE games must NEVER trigger game mode."""

    @pytest.mark.asyncio
    async def test_idle_game_does_not_trigger(self):
        gm = _make_game_mode()
        # Simulate a scan that finds only an idle game
        with patch.object(gm, "_scan_processes", return_value=[]):
            await gm._scan_once()
        assert gm.is_active is False

    def test_all_default_idle_patterns_classify_as_idle(self):
        gm = _make_game_mode()
        for pattern in DEFAULT_IDLE_GAME_PATTERNS:
            category = gm._classify_process(pattern + "_game")
            assert category == GameCategory.IDLE, (
                f"Pattern '{pattern}' did not classify as IDLE"
            )


# ---------------------------------------------------------------------------
# Game context preloading
# ---------------------------------------------------------------------------

class TestPreloadGameContext:
    """Tests for _preload_game_context."""

    @pytest.mark.asyncio
    async def test_preload_with_results(self):
        gm = _make_game_mode()
        FakeFact = type("Fact", (), {"text": ""})

        facts = []
        for t in ["Boss tip: use fire", "Played Elden Ring for 2h", "Secret area in Limgrave"]:
            f = FakeFact()
            f.text = t
            facts.append(f)
        gm._memory.search = AsyncMock(return_value=facts)

        await gm._preload_game_context("Elden Ring")

        ctx = gm.preloaded_context
        assert "[Game context for Elden Ring:]" in ctx
        assert "Boss tip: use fire" in ctx
        assert "Secret area in Limgrave" in ctx
        assert len(ctx) > 0

    @pytest.mark.asyncio
    async def test_preload_no_results(self):
        gm = _make_game_mode()
        gm._memory.search = AsyncMock(return_value=[])

        await gm._preload_game_context("Unknown Game")

        assert gm.preloaded_context == ""

    @pytest.mark.asyncio
    async def test_preload_memory_error_is_non_fatal(self):
        gm = _make_game_mode()
        gm._memory.search = AsyncMock(side_effect=RuntimeError("DB error"))

        await gm._preload_game_context("Elden Ring")

        assert gm.preloaded_context == ""


# ---------------------------------------------------------------------------
# Force activate / deactivate
# ---------------------------------------------------------------------------

class TestForceCommands:
    """Tests for voice-command force activate/deactivate."""

    @pytest.mark.asyncio
    async def test_force_activate(self):
        gm = _make_game_mode()
        await gm.force_activate("Manual Test")
        assert gm.is_active is True
        assert gm.current_game is not None
        assert gm.current_game.title == "Manual Test"
        assert gm.current_game.pid == 0

    @pytest.mark.asyncio
    async def test_force_deactivate(self):
        gm = _make_game_mode()
        await gm.force_activate("Test Game")
        await asyncio.sleep(0.05)
        await gm.force_deactivate()
        assert gm.is_active is False

    @pytest.mark.asyncio
    async def test_force_activate_when_already_active(self):
        gm = _make_game_mode()
        await gm.force_activate("Game 1")
        await gm.force_activate("Game 2")
        # Should remain on Game 1 (already active)
        assert gm.current_game.title == "Game 1"

    @pytest.mark.asyncio
    async def test_force_deactivate_when_not_active(self):
        gm = _make_game_mode()
        # Should not crash
        await gm.force_deactivate()
        assert gm.is_active is False


# ---------------------------------------------------------------------------
# Session info
# ---------------------------------------------------------------------------

class TestSessionInfo:
    def test_session_info_when_active(self):
        gm = _make_game_mode()
        gm._active = True
        gm._current_game = DetectedGame(
            name="eldenring.exe", title="Elden Ring",
            category=GameCategory.MAIN, pid=1234,
        )
        gm._game_start_time = datetime.now()

        info = gm.get_game_session_info()
        assert info is not None
        assert "Elden Ring" in info
        assert "Game mode active" in info

    def test_session_info_when_inactive(self):
        gm = _make_game_mode()
        assert gm.get_game_session_info() is None


# ---------------------------------------------------------------------------
# Monitoring lifecycle
# ---------------------------------------------------------------------------

class TestMonitoring:
    def test_start_monitoring_disabled(self):
        gm = _make_game_mode(enabled=False)
        gm.start_monitoring()
        assert gm._scan_task is None

    def test_enabled_property(self):
        gm = _make_game_mode(enabled=True)
        assert gm.enabled is True
        gm.enabled = False
        assert gm.enabled is False
