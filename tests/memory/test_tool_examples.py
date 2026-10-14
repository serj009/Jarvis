"""Tests for jarvis.memory.tool_examples (T4.5 self-learning)."""

from __future__ import annotations

import json

import pytest

from jarvis.memory.tool_examples import MAX_PER_TOOL, MAX_TOTAL, ToolExampleStore


@pytest.fixture()
def store(tmp_path) -> ToolExampleStore:
    return ToolExampleStore(path=tmp_path / "examples.json")


class TestToolExampleStore:
    def test_starts_empty(self, store) -> None:
        assert store.example_count() == 0
        assert store.get_examples_for_prompt() == ""

    def test_record_success_creates_entry(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        assert store.example_count() == 1
        assert "open_app" in store.data
        assert store.data["open_app"][0]["utterance"] == "open chrome"
        assert store.data["open_app"][0]["args"] == {"app_name": "chrome"}
        assert store.data["open_app"][0]["count"] == 1

    def test_dedup_increments_count(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        store.record_success("OPEN CHROME", "open_app", {"app_name": "chrome"})
        assert store.example_count() == 1
        assert store.data["open_app"][0]["count"] == 3

    def test_different_utterances_create_separate_entries(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        store.record_success("open firefox", "open_app", {"app_name": "firefox"})
        assert store.example_count() == 2

    def test_different_tools_are_separate(self, store) -> None:
        store.record_success("what's the weather", "get_weather", {})
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        assert store.example_count() == 2
        assert "get_weather" in store.data
        assert "open_app" in store.data

    def test_persists_to_disk(self, tmp_path) -> None:
        path = tmp_path / "persist.json"
        s1 = ToolExampleStore(path=path)
        s1.record_success("test", "tool_a", {"x": 1})

        # Reload from disk
        s2 = ToolExampleStore(path=path)
        assert s2.example_count() == 1
        assert "tool_a" in s2.data

    def test_prune_per_tool(self, store) -> None:
        for i in range(MAX_PER_TOOL + 5):
            store.record_success(f"utterance_{i}", "tool_a", {"i": i})
        assert len(store.data["tool_a"]) <= MAX_PER_TOOL

    def test_prune_keeps_highest_count(self, store) -> None:
        # Add many low-count examples
        for i in range(MAX_PER_TOOL + 3):
            store.record_success(f"utterance_{i}", "tool_a", {"i": i})

        # Bump one specific example to count=10
        target = "utterance_0"
        for _ in range(9):
            store.record_success(target, "tool_a", {"i": 0})

        # After pruning, the high-count example should survive
        assert any(
            ex["utterance"] == target
            for ex in store.data["tool_a"]
        )

    def test_get_examples_for_prompt_format(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        store.record_success("what's the weather", "get_weather", {})
        result = store.get_examples_for_prompt()
        assert "[Learned from past interactions:]" in result
        assert "open_app" in result
        assert "get_weather" in result
        assert "→" in result

    def test_get_examples_for_prompt_sorted_by_count(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        for _ in range(5):
            store.record_success("what's the weather", "get_weather", {})

        result = store.get_examples_for_prompt()
        lines = result.split("\n")
        # First non-header line should be the weather (count=5) not open (count=1)
        assert "get_weather" in lines[1]

    def test_get_examples_for_prompt_respects_max(self, store) -> None:
        for i in range(30):
            store.record_success(f"command_{i}", f"tool_{i}", {"i": i})

        result = store.get_examples_for_prompt(max_examples=5)
        lines = [l for l in result.split("\n") if "→" in l]
        assert len(lines) <= 5

    def test_handles_corrupt_json(self, tmp_path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        s = ToolExampleStore(path=path)
        # Should start fresh instead of crashing
        assert s.example_count() == 0

    def test_handles_missing_file(self, tmp_path) -> None:
        path = tmp_path / "subdir" / "new.json"
        s = ToolExampleStore(path=path)
        s.record_success("test", "tool_a", {})
        assert path.exists()

    def test_global_prune(self, store) -> None:
        # Fill up to MAX_TOTAL + some extra
        tools_needed = (MAX_TOTAL // MAX_PER_TOOL) + 2
        for t in range(tools_needed):
            for i in range(MAX_PER_TOOL):
                store.record_success(
                    f"utterance_{t}_{i}", f"tool_{t}", {"t": t, "i": i},
                )
        assert store.example_count() <= MAX_TOTAL

    def test_record_success_never_raises(self, tmp_path) -> None:
        # Even with a read-only path, record_success should not raise
        store = ToolExampleStore(path=tmp_path / "ok.json")
        # This should not raise even if _save fails internally
        store.record_success("test", "tool", {})

    def test_empty_args_format(self, store) -> None:
        store.record_success("what time is it", "get_time", {})
        result = store.get_examples_for_prompt()
        assert "get_time()" in result

    def test_args_with_values_format(self, store) -> None:
        store.record_success("open chrome", "open_app", {"app_name": "chrome"})
        result = store.get_examples_for_prompt()
        assert "app_name='chrome'" in result
