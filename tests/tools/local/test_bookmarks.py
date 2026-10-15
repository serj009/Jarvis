"""Tests for jarvis.tools.local.bookmarks.

Uses a temp directory for bookmark storage — no real files are touched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.bookmarks import (
    BookmarksTool,
    BookmarksArgs,
    _load_bookmarks,
    _save_bookmarks,
)
from jarvis.tools.registry import VoicePattern


# -- protocol conformance -----------------------------------------------


class TestProtocol:
    def test_has_required_attributes(self):
        tool = BookmarksTool()
        assert tool.name == "bookmarks"
        assert tool.description
        assert tool.args_schema is BookmarksArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(BookmarksTool, "voice_patterns")
        assert isinstance(BookmarksTool.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in BookmarksTool.voice_patterns)
        assert len(BookmarksTool.voice_patterns) >= 9


# -- voice pattern matching ---------------------------------------------


class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "save to bookmarks",
        "add bookmark",
        "show bookmarks",
        "list bookmarks",
        "open bookmark github",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BookmarksTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "сохрани в закладки",
        "покажи закладки",
        "запомни страницу",
        "запомни эту страницу",
        "открой закладку github",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BookmarksTool.voice_patterns
        )
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "збережи в закладки",
        "покажи закладки",
        "запам'ятай сторінку",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in BookmarksTool.voice_patterns
        )
        assert matched, f"No UK pattern matched: {utterance!r}"


# -- storage functions --------------------------------------------------


class TestStorage:
    def test_load_empty_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.bookmarks._BOOKMARKS_FILE",
            tmp_path / "nonexistent.json",
        )
        assert _load_bookmarks() == []

    def test_save_and_load(self, tmp_path, monkeypatch):
        bm_file = tmp_path / "data" / "bookmarks.json"
        monkeypatch.setattr("jarvis.tools.local.bookmarks._BOOKMARKS_DIR", tmp_path / "data")
        monkeypatch.setattr("jarvis.tools.local.bookmarks._BOOKMARKS_FILE", bm_file)

        data = [{"name": "Test", "url": "https://test.com"}]
        _save_bookmarks(data)
        assert bm_file.exists()
        loaded = _load_bookmarks()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Test"


# -- execute dispatch ---------------------------------------------------


@pytest.fixture(autouse=True)
def _temp_bookmarks(tmp_path, monkeypatch):
    bm_file = tmp_path / "data" / "bookmarks.json"
    monkeypatch.setattr("jarvis.tools.local.bookmarks._BOOKMARKS_DIR", tmp_path / "data")
    monkeypatch.setattr("jarvis.tools.local.bookmarks._BOOKMARKS_FILE", bm_file)
    # Mock browser
    mock_bc = MagicMock()
    mock_bc.driver_active = False
    monkeypatch.setattr(
        "jarvis.tools.local.browser_tabs._get_browser",
        lambda: mock_bc,
    )
    return bm_file


class TestExecute:
    @pytest.mark.asyncio
    async def test_save_with_url(self, _temp_bookmarks):
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(
            action="save", url="https://github.com", name="GitHub",
        ))
        assert result.success
        assert "GitHub" in result.output

        bm = _load_bookmarks()
        assert len(bm) == 1
        assert bm[0]["url"] == "https://github.com"

    @pytest.mark.asyncio
    async def test_save_without_url_no_browser(self, _temp_bookmarks):
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="save"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_list_empty(self, _temp_bookmarks):
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="list"))
        assert result.success
        assert "no bookmark" in result.output.lower() or "0" in result.output

    @pytest.mark.asyncio
    async def test_list_with_items(self, _temp_bookmarks):
        _save_bookmarks([
            {"name": "Google", "url": "https://google.com"},
            {"name": "GitHub", "url": "https://github.com"},
        ])
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="list"))
        assert result.success
        assert "2" in result.output

    @pytest.mark.asyncio
    @patch("jarvis.tools.local.bookmarks.webbrowser")
    async def test_open_bookmark(self, mock_wb, _temp_bookmarks):
        _save_bookmarks([{"name": "GitHub", "url": "https://github.com"}])
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="open", name="github"))
        assert result.success
        mock_wb.open.assert_called_once_with("https://github.com")

    @pytest.mark.asyncio
    async def test_open_not_found(self, _temp_bookmarks):
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="open", name="nonexistent"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_delete_bookmark(self, _temp_bookmarks):
        _save_bookmarks([{"name": "GitHub", "url": "https://github.com"}])
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="delete", name="github"))
        assert result.success
        assert _load_bookmarks() == []

    @pytest.mark.asyncio
    async def test_delete_not_found(self, _temp_bookmarks):
        tool = BookmarksTool()
        result = await tool.execute(BookmarksArgs(action="delete", name="nope"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_save_deduplicates_by_url(self, _temp_bookmarks):
        tool = BookmarksTool()
        await tool.execute(BookmarksArgs(action="save", url="https://x.com", name="X"))
        await tool.execute(BookmarksArgs(action="save", url="https://x.com", name="X v2"))
        bm = _load_bookmarks()
        assert len(bm) == 1
        assert bm[0]["name"] == "X v2"
