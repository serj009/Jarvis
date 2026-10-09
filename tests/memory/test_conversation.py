"""Tests for jarvis.memory.conversation (T4.2 ConversationMemory)."""

from __future__ import annotations

import pytest

from jarvis.memory.conversation import ConversationMemory, ConversationTurn


@pytest.fixture()
async def conv_mem(tmp_path) -> ConversationMemory:
    cm = ConversationMemory(root_path=tmp_path / "conversations")
    await cm.open()
    yield cm
    await cm.close()


class TestConversationMemory:
    @pytest.mark.asyncio()
    async def test_open_creates_directory(self, tmp_path) -> None:
        cm = ConversationMemory(root_path=tmp_path / "conv")
        await cm.open()
        assert cm.is_open
        assert (tmp_path / "conv").exists()
        assert (tmp_path / "conv" / "archive").exists()
        await cm.close()

    @pytest.mark.asyncio()
    async def test_log_turn_returns_id(self, conv_mem) -> None:
        turn_id = await conv_mem.log_turn("Hello", "Hi, sir.")
        assert isinstance(turn_id, str)
        assert len(turn_id) == 12

    @pytest.mark.asyncio()
    async def test_recent_returns_logged_turns(self, conv_mem) -> None:
        await conv_mem.log_turn("What time is it?", "It's 3 PM, sir.")
        await conv_mem.log_turn("Thanks", "You're welcome, sir.")

        turns = await conv_mem.recent(limit=10)
        assert len(turns) == 2
        assert turns[0].user_text == "What time is it?"
        assert turns[1].assistant_text == "You're welcome, sir."

    @pytest.mark.asyncio()
    async def test_search_finds_matching(self, conv_mem) -> None:
        await conv_mem.log_turn("What's the weather?", "Sunny, 20 degrees.")
        await conv_mem.log_turn("Open YouTube", "Opening YouTube, sir.")

        results = await conv_mem.search("weather")
        assert len(results) >= 1
        assert "weather" in results[0].user_text.lower()

    @pytest.mark.asyncio()
    async def test_count(self, conv_mem) -> None:
        assert await conv_mem.count() == 0
        await conv_mem.log_turn("Q", "A")
        assert await conv_mem.count() == 1

    @pytest.mark.asyncio()
    async def test_session_id_set(self, conv_mem) -> None:
        assert len(conv_mem.session_id) == 8

    @pytest.mark.asyncio()
    async def test_turns_have_session_id(self, conv_mem) -> None:
        await conv_mem.log_turn("Hi", "Hello")
        turns = await conv_mem.recent(limit=1)
        assert turns[0].session_id == conv_mem.session_id
