"""Tests for jarvis.tools.local.rss_reader.

Uses temp directory for RSS storage and mocked httpx for feed fetching.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.rss_reader import (
    GetLatestNewsArgs,
    GetLatestNewsTool,
    ListRssFeedsArgs,
    ListRssFeedsTool,
    SubscribeRssArgs,
    SubscribeRssTool,
    UnsubscribeRssArgs,
    UnsubscribeRssTool,
    _load_feeds,
    _parse_feed_xml,
    _save_feeds,
)
from jarvis.tools.registry import ToolResult, VoicePattern

# ---------------------------------------------------------------------------
# Sample RSS/Atom XML
# ---------------------------------------------------------------------------

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>First Post</title>
      <link>https://example.com/1</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>First post summary</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/2</link>
      <description>Second post summary</description>
    </item>
  </channel>
</rss>"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://atom.example.com/1" />
    <published>2024-01-01T12:00:00Z</published>
    <summary>Atom summary</summary>
  </entry>
</feed>"""


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    @pytest.mark.parametrize("cls,name,schema", [
        (SubscribeRssTool, "subscribe_rss", SubscribeRssArgs),
        (UnsubscribeRssTool, "unsubscribe_rss", UnsubscribeRssArgs),
        (ListRssFeedsTool, "list_rss_feeds", ListRssFeedsArgs),
        (GetLatestNewsTool, "get_latest_news", GetLatestNewsArgs),
    ])
    def test_has_required_attributes(self, cls, name, schema):
        tool = cls()
        assert tool.name == name
        assert tool.description
        assert tool.args_schema is schema
        assert tool.requires_confirmation is False

    @pytest.mark.parametrize("cls", [
        SubscribeRssTool, UnsubscribeRssTool,
        ListRssFeedsTool, GetLatestNewsTool,
    ])
    def test_has_voice_patterns(self, cls):
        assert hasattr(cls, "voice_patterns")
        assert isinstance(cls.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in cls.voice_patterns)
        assert len(cls.voice_patterns) >= 2


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "subscribe to this rss feed",
        "subscribe to https://example.com/feed",
        "what's new",
        "show news",
        "latest news",
        "show me the news",
        "unsubscribe from techcrunch",
        "list feeds",
        "show my subscriptions",
    ])
    def test_en_patterns_match(self, utterance):
        all_patterns = (
            SubscribeRssTool.voice_patterns
            + UnsubscribeRssTool.voice_patterns
            + ListRssFeedsTool.voice_patterns
            + GetLatestNewsTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "подпишись на этот сайт",
        "что нового",
        "покажи новости",
        "отпишись от techcrunch",
        "покажи подписки",
    ])
    def test_ru_patterns_match(self, utterance):
        all_patterns = (
            SubscribeRssTool.voice_patterns
            + UnsubscribeRssTool.voice_patterns
            + ListRssFeedsTool.voice_patterns
            + GetLatestNewsTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "підпишись на цей сайт",
        "що нового",
        "покажи новини",
        "відпишись від techcrunch",
    ])
    def test_uk_patterns_match(self, utterance):
        all_patterns = (
            SubscribeRssTool.voice_patterns
            + UnsubscribeRssTool.voice_patterns
            + ListRssFeedsTool.voice_patterns
            + GetLatestNewsTool.voice_patterns
        )
        matched = any(re.match(vp.regex, utterance) for vp in all_patterns)
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

class TestFeedParsing:
    def test_parse_rss(self):
        items = _parse_feed_xml(SAMPLE_RSS)
        assert len(items) == 2
        assert items[0]["title"] == "First Post"
        assert items[0]["link"] == "https://example.com/1"
        assert items[1]["title"] == "Second Post"

    def test_parse_atom(self):
        items = _parse_feed_xml(SAMPLE_ATOM)
        assert len(items) == 1
        assert items[0]["title"] == "Atom Entry"
        assert items[0]["link"] == "https://atom.example.com/1"

    def test_parse_invalid_xml(self):
        items = _parse_feed_xml("<not valid xml>>>")
        assert items == []

    def test_parse_empty(self):
        items = _parse_feed_xml("")
        assert items == []


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _temp_feeds(tmp_path, monkeypatch):
    # Data dir resolved via jarvis.paths; repoint with JARVIS_DATA_DIR and
    # reset the lazy store. Layout unchanged: <data_dir>/data/rss_feeds.json.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("jarvis.tools.local.rss_reader._RSS_STORE", None)


class TestStorage:
    def test_load_empty(self):
        assert _load_feeds() == []

    def test_save_and_load(self):
        feeds = [{"name": "Test", "url": "https://example.com/feed"}]
        _save_feeds(feeds)
        loaded = _load_feeds()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Test"


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------

def _mock_httpx_get(url, **kwargs):
    """Mock httpx.get that returns RSS XML."""
    resp = MagicMock()
    resp.text = SAMPLE_RSS
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_success(self):
        with patch("jarvis.tools.local.rss_reader._fetch_feed", return_value=(SAMPLE_RSS, None)):
            tool = SubscribeRssTool()
            result = await tool.execute(SubscribeRssArgs(
                url="https://example.com/feed",
            ))
            assert result.success
            assert "Subscribed" in result.output
            # Verify stored
            feeds = _load_feeds()
            assert len(feeds) == 1

    @pytest.mark.asyncio
    async def test_subscribe_no_url(self):
        tool = SubscribeRssTool()
        result = await tool.execute(SubscribeRssArgs(url=""))
        assert not result.success

    @pytest.mark.asyncio
    async def test_subscribe_deduplicates(self):
        with patch("jarvis.tools.local.rss_reader._fetch_feed", return_value=(SAMPLE_RSS, None)):
            tool = SubscribeRssTool()
            await tool.execute(SubscribeRssArgs(url="https://example.com/feed"))
            await tool.execute(SubscribeRssArgs(url="https://example.com/feed"))
            feeds = _load_feeds()
            assert len(feeds) == 1


class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        _save_feeds([{"name": "Test", "url": "https://example.com/feed"}])
        tool = UnsubscribeRssTool()
        result = await tool.execute(UnsubscribeRssArgs(name="Test"))
        assert result.success
        assert _load_feeds() == []

    @pytest.mark.asyncio
    async def test_unsubscribe_not_found(self):
        tool = UnsubscribeRssTool()
        result = await tool.execute(UnsubscribeRssArgs(name="nonexistent"))
        assert not result.success


class TestListFeeds:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        tool = ListRssFeedsTool()
        result = await tool.execute(ListRssFeedsArgs())
        assert result.success
        assert "No RSS" in result.output

    @pytest.mark.asyncio
    async def test_list_with_feeds(self):
        _save_feeds([
            {"name": "Feed A", "url": "https://a.com/feed"},
            {"name": "Feed B", "url": "https://b.com/feed"},
        ])
        tool = ListRssFeedsTool()
        result = await tool.execute(ListRssFeedsArgs())
        assert result.success
        assert "Feed A" in result.output
        assert "Feed B" in result.output
        assert "(2)" in result.output


class TestGetNews:
    @pytest.mark.asyncio
    async def test_get_news_empty(self):
        tool = GetLatestNewsTool()
        result = await tool.execute(GetLatestNewsArgs())
        assert result.success
        assert "No RSS" in result.output

    @pytest.mark.asyncio
    async def test_get_news_with_feeds(self):
        _save_feeds([{"name": "Test", "url": "https://example.com/feed"}])
        with patch("jarvis.tools.local.rss_reader._fetch_feed") as mock_fetch:
            mock_fetch.return_value = (SAMPLE_RSS, None)
            tool = GetLatestNewsTool()
            result = await tool.execute(GetLatestNewsArgs())
            assert result.success
            assert "First Post" in result.output
            assert "Second Post" in result.output

    @pytest.mark.asyncio
    async def test_get_news_fetch_error(self):
        _save_feeds([{"name": "Bad", "url": "https://bad.com/feed"}])
        with patch("jarvis.tools.local.rss_reader._fetch_feed") as mock_fetch:
            mock_fetch.return_value = (None, "Connection error")
            tool = GetLatestNewsTool()
            result = await tool.execute(GetLatestNewsArgs())
            assert result.success  # Partial results still succeed
            assert "Connection error" in result.output
