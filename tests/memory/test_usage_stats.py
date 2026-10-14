"""Tests for jarvis.memory.usage_stats — record, top_apps, top_tools, active_hours."""

from __future__ import annotations

import json

import pytest

from jarvis.memory.usage_stats import UsageStats


@pytest.fixture()
def stats(tmp_path) -> UsageStats:
    return UsageStats(path=tmp_path / "usage_stats.json")


class TestUsageStats:
    def test_starts_empty(self, stats) -> None:
        assert stats.top_apps() == []
        assert stats.top_tools() == []
        assert stats.active_hours() == []

    def test_record_app_use(self, stats) -> None:
        stats.record_app_use("chrome")
        top = stats.top_apps()
        assert len(top) == 1
        assert top[0] == ("chrome", 1)

    def test_record_app_use_case_insensitive(self, stats) -> None:
        stats.record_app_use("Chrome")
        stats.record_app_use("chrome")
        stats.record_app_use("CHROME")
        top = stats.top_apps()
        assert len(top) == 1
        assert top[0] == ("chrome", 3)

    def test_record_app_use_strips_whitespace(self, stats) -> None:
        stats.record_app_use("  chrome  ")
        top = stats.top_apps()
        assert top[0][0] == "chrome"

    def test_record_app_use_empty_ignored(self, stats) -> None:
        stats.record_app_use("")
        stats.record_app_use("   ")
        assert stats.top_apps() == []

    def test_record_tool_use(self, stats) -> None:
        stats.record_tool_use("open_app")
        top = stats.top_tools()
        assert len(top) == 1
        assert top[0] == ("open_app", 1)

    def test_top_apps_ordering(self, stats) -> None:
        for _ in range(5):
            stats.record_app_use("chrome")
        for _ in range(3):
            stats.record_app_use("vscode")
        for _ in range(1):
            stats.record_app_use("steam")

        top = stats.top_apps(3)
        assert len(top) == 3
        assert top[0] == ("chrome", 5)
        assert top[1] == ("vscode", 3)
        assert top[2] == ("steam", 1)

    def test_top_apps_limit(self, stats) -> None:
        for name in ["a", "b", "c", "d", "e"]:
            stats.record_app_use(name)

        top = stats.top_apps(2)
        assert len(top) == 2

    def test_top_tools_ordering(self, stats) -> None:
        for _ in range(10):
            stats.record_tool_use("open_app")
        for _ in range(3):
            stats.record_tool_use("get_weather")

        top = stats.top_tools()
        assert top[0] == ("open_app", 10)
        assert top[1] == ("get_weather", 3)

    def test_active_hours(self, stats) -> None:
        stats.record_tool_use("open_app")
        hours = stats.active_hours()
        # At least one hour should be recorded
        assert len(hours) >= 1
        assert all(isinstance(h, int) for h in hours)

    def test_for_prompt_empty_returns_none(self, stats) -> None:
        assert stats.for_prompt() is None

    def test_for_prompt_with_apps(self, stats) -> None:
        for _ in range(5):
            stats.record_app_use("chrome")
        for _ in range(3):
            stats.record_app_use("vscode")

        result = stats.for_prompt()
        assert result is not None
        assert "chrome (5×)" in result
        assert "vscode (3×)" in result
        assert result.startswith("[User's frequently used apps:")

    def test_persistence(self, tmp_path) -> None:
        path = tmp_path / "stats.json"

        s1 = UsageStats(path=path)
        s1.record_app_use("chrome")
        s1.record_app_use("chrome")
        s1.record_tool_use("open_app")

        s2 = UsageStats(path=path)
        assert s2.top_apps() == [("chrome", 2)]
        assert s2.top_tools() == [("open_app", 1)]

    def test_corrupted_file_starts_fresh(self, tmp_path) -> None:
        path = tmp_path / "stats.json"
        path.write_text("not json!!!", encoding="utf-8")

        s = UsageStats(path=path)
        assert s.top_apps() == []
        # Should still work
        s.record_app_use("chrome")
        assert s.top_apps() == [("chrome", 1)]

    def test_multiple_tools(self, stats) -> None:
        stats.record_tool_use("open_app")
        stats.record_tool_use("get_weather")
        stats.record_tool_use("screenshot")
        stats.record_tool_use("open_app")

        top = stats.top_tools(5)
        assert len(top) == 3
        assert top[0] == ("open_app", 2)
