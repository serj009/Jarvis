"""Tests for jarvis.memory.session_summary (T4.7 session summary persistence)."""

from __future__ import annotations

import pytest

from jarvis.memory.session_summary import (
    MAX_SUMMARY_LENGTH,
    save_session_summary,
    summarize_session,
)
from jarvis.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# summarize_session (synchronous, no store needed)
# ---------------------------------------------------------------------------

class TestSummarizeSession:
    def test_too_short_returns_none(self) -> None:
        """Sessions with < 2 user turns are not worth summarising."""
        turns = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        assert summarize_session(turns) is None

    def test_empty_turns_returns_none(self) -> None:
        assert summarize_session([]) is None

    def test_no_user_turns_returns_none(self) -> None:
        turns = [
            {"role": "assistant", "content": "How can I help?"},
        ]
        assert summarize_session(turns) is None

    def test_minimal_session(self) -> None:
        turns = [
            {"role": "user", "content": "open chrome"},
            {"role": "assistant", "content": "Done, sir."},
            {"role": "user", "content": "what's the weather?"},
            {"role": "assistant", "content": "It's sunny, 22°C."},
        ]
        result = summarize_session(turns)
        assert result is not None
        assert "Session" in result
        assert "open chrome" in result
        assert "weather" in result

    def test_includes_tool_calls(self) -> None:
        turns = [
            {"role": "user", "content": "open chrome"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "open_app", "arguments": {"app_name": "chrome"}},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "open_app", "content": "Done"},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "You're welcome!"},
        ]
        result = summarize_session(turns)
        assert result is not None
        assert "open_app" in result
        assert "Tools used:" in result

    def test_user_topics_truncated(self) -> None:
        """Long user messages are truncated to 80 chars."""
        long_msg = "x" * 200
        turns = [
            {"role": "user", "content": long_msg},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "ok"},
        ]
        result = summarize_session(turns)
        assert result is not None
        assert "..." in result  # truncated

    def test_max_5_user_topics(self) -> None:
        turns = []
        for i in range(10):
            turns.append({"role": "user", "content": f"topic {i}"})
            turns.append({"role": "assistant", "content": "ok"})
        result = summarize_session(turns)
        assert result is not None
        # Should contain topics 0-4 but not 5-9
        assert "topic 0" in result
        assert "topic 4" in result
        # topic 5 may not appear (max 5 topics)

    def test_max_5_unique_tools(self) -> None:
        turns = []
        for i in range(8):
            turns.append({"role": "user", "content": f"do thing {i}"})
            turns.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": f"tool_{i}", "arguments": {}}},
                ],
            })
        result = summarize_session(turns)
        assert result is not None
        assert "tool_0" in result
        assert "tool_4" in result

    def test_deduplicates_tools(self) -> None:
        turns = [
            {"role": "user", "content": "open chrome"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "open_app", "arguments": {}}},
            ]},
            {"role": "user", "content": "open again"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "open_app", "arguments": {}}},
            ]},
        ]
        result = summarize_session(turns)
        assert result is not None
        # "open_app" should appear only once
        assert result.count("open_app") == 1

    def test_summary_length_capped(self) -> None:
        turns = []
        for i in range(50):
            turns.append({"role": "user", "content": f"topic number {i} with extra text here"})
            turns.append({"role": "assistant", "content": "understood"})
        result = summarize_session(turns)
        assert result is not None
        assert len(result) <= MAX_SUMMARY_LENGTH

    def test_handles_malformed_tool_calls(self) -> None:
        """Non-dict tool_calls entries should not crash."""
        turns = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "", "tool_calls": ["not_a_dict", None]},
            {"role": "user", "content": "and another thing"},
            {"role": "assistant", "content": "done"},
        ]
        result = summarize_session(turns)
        assert result is not None  # should not crash

    def test_date_format(self) -> None:
        turns = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
            {"role": "assistant", "content": "goodbye"},
        ]
        result = summarize_session(turns)
        assert result is not None
        # Should start with "Session YYYY-MM-DD HH:MM:"
        assert result.startswith("Session 20")


# ---------------------------------------------------------------------------
# save_session_summary (async, needs store)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


class TestSaveSessionSummary:
    @pytest.mark.asyncio
    async def test_saves_to_store(self, store: MemoryStore) -> None:
        turns = [
            {"role": "user", "content": "open chrome"},
            {"role": "assistant", "content": "Done, sir."},
            {"role": "user", "content": "what's the weather?"},
            {"role": "assistant", "content": "It's sunny."},
        ]
        result = await save_session_summary(store, turns)
        assert result is True

        facts = await store.search("Session", category="session", limit=5)
        assert len(facts) >= 1
        assert facts[0].category == "session"
        assert "session_summary" in facts[0].tags

    @pytest.mark.asyncio
    async def test_returns_false_for_short_session(self, store: MemoryStore) -> None:
        turns = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = await save_session_summary(store, turns)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_empty(self, store: MemoryStore) -> None:
        result = await save_session_summary(store, [])
        assert result is False

    @pytest.mark.asyncio
    async def test_source_is_system(self, store: MemoryStore) -> None:
        turns = [
            {"role": "user", "content": "task one"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "task two"},
            {"role": "assistant", "content": "done"},
        ]
        await save_session_summary(store, turns)
        facts = await store.search("Session", category="session", limit=5)
        assert len(facts) >= 1
        assert facts[0].source == "system"

    @pytest.mark.asyncio
    async def test_never_raises_on_closed_store(self, tmp_path) -> None:
        """save_session_summary should return False, not raise, on failure."""
        store = MemoryStore(db_path=tmp_path / "closed.db")
        turns = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        result = await save_session_summary(store, turns)
        assert result is False
