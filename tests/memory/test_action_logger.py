"""Tests for jarvis.memory.action_logger (T4.7 action logging)."""

from __future__ import annotations

import pytest

from jarvis.memory.action_logger import format_action, log_action, _SKIP_TOOLS
from jarvis.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# format_action (synchronous, no store needed)
# ---------------------------------------------------------------------------

class TestFormatAction:
    def test_open_app(self) -> None:
        result = format_action("open_app", {"app_name": "chrome"})
        assert result == "Opened chrome"

    def test_close_app(self) -> None:
        result = format_action("close_app", {"app_name": "notepad"})
        assert result == "Closed notepad"

    def test_play_youtube_music(self) -> None:
        result = format_action("play_youtube_music", {"query": "jazz playlist"})
        assert result == "Played music: jazz playlist"

    def test_launch_steam_game(self) -> None:
        result = format_action("launch_steam_game", {"game_name": "Mafia 2"})
        assert result == "Launched game: Mafia 2 on Steam"

    def test_open_url(self) -> None:
        result = format_action("open_url", {"query": "python docs"})
        assert result == "Searched for: python docs"

    def test_get_weather_no_args(self) -> None:
        result = format_action("get_weather", {})
        assert result == "Checked weather"

    def test_screenshot(self) -> None:
        result = format_action("screenshot", {})
        assert result == "Took a screenshot"

    def test_volume(self) -> None:
        result = format_action("volume", {"action": "up"})
        assert result == "Changed volume: up"

    def test_take_note(self) -> None:
        result = format_action("take_note", {"title": "Shopping list"})
        assert result == "Created a note: Shopping list"

    def test_deep_research(self) -> None:
        result = format_action("deep_research", {"query": "AI trends"})
        assert result == "Started deep research: AI trends"

    def test_unknown_tool_generic_fallback(self) -> None:
        result = format_action("some_new_tool", {})
        assert result == "Used some_new_tool"

    def test_skip_tool_returns_none(self) -> None:
        for tool in ("clipboard", "search_memory", "memory_status", "forget_fact",
                      "show_dashboard", "close_dashboard"):
            assert format_action(tool, {}) is None, f"{tool} should be skipped"

    def test_template_missing_key_graceful(self) -> None:
        # open_app expects app_name, pass wrong key
        result = format_action("open_app", {"wrong_key": "chrome"})
        assert result is not None
        assert "Opened" in result

    def test_template_with_colon_fallback(self) -> None:
        # play_youtube_music expects query — missing → fallback before colon
        result = format_action("play_youtube_music", {})
        assert result == "Played music"

    def test_skip_tools_are_frozenset(self) -> None:
        assert isinstance(_SKIP_TOOLS, frozenset)


# ---------------------------------------------------------------------------
# log_action (async, needs store)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


class TestLogAction:
    @pytest.mark.asyncio
    async def test_successful_action_saved(self, store: MemoryStore) -> None:
        await log_action(store, "open_app", {"app_name": "chrome"}, success=True)
        facts = await store.search("Opened chrome", limit=5)
        assert len(facts) >= 1
        assert facts[0].category == "action"
        assert "action" in facts[0].tags
        assert "open_app" in facts[0].tags

    @pytest.mark.asyncio
    async def test_failed_action_not_saved(self, store: MemoryStore) -> None:
        await log_action(store, "open_app", {"app_name": "chrome"}, success=False)
        facts = await store.search("Opened chrome", limit=5)
        assert len(facts) == 0

    @pytest.mark.asyncio
    async def test_skipped_tool_not_saved(self, store: MemoryStore) -> None:
        await log_action(store, "clipboard", {}, success=True)
        facts = await store.search("clipboard", limit=5)
        assert len(facts) == 0

    @pytest.mark.asyncio
    async def test_unknown_tool_saved_with_generic(self, store: MemoryStore) -> None:
        await log_action(store, "custom_tool", {"x": 1}, success=True)
        facts = await store.search("Used custom_tool", limit=5)
        assert len(facts) >= 1

    @pytest.mark.asyncio
    async def test_source_is_system(self, store: MemoryStore) -> None:
        await log_action(store, "screenshot", {}, success=True)
        facts = await store.search("screenshot", limit=5)
        assert len(facts) >= 1
        assert facts[0].source == "system"

    @pytest.mark.asyncio
    async def test_never_raises(self, tmp_path) -> None:
        """log_action should not raise even with a closed store."""
        store = MemoryStore(db_path=tmp_path / "closed.db")
        # store not opened — should not raise
        await log_action(store, "open_app", {"app_name": "x"}, success=True)
