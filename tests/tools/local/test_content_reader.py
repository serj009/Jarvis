"""Tests for jarvis.tools.local.content_reader.

All network and browser interactions are mocked.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.content_reader import (
    ContentReaderArgs,
    ContentReaderTool,
    _extract_readable,
    _extract_title,
    _httpx_fetch,
)
from jarvis.tools.registry import VoicePattern

# -- protocol conformance -----------------------------------------------


class TestProtocol:
    def test_has_required_attributes(self):
        tool = ContentReaderTool()
        assert tool.name == "content_reader"
        assert tool.description
        assert tool.args_schema is ContentReaderArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(ContentReaderTool, "voice_patterns")
        assert isinstance(ContentReaderTool.voice_patterns, tuple)
        assert all(isinstance(p, VoicePattern) for p in ContentReaderTool.voice_patterns)
        assert len(ContentReaderTool.voice_patterns) >= 9


# -- voice pattern matching ---------------------------------------------


class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "read this page",
        "read page",
        "what's this page about?",
        "copy text",
        "copy page text",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in ContentReaderTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "прочитай страницу",
        "прочитай эту страницу",
        "скопируй текст страницы",
        "сохрани как pdf",
        "сохрани страницу как пдф",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in ContentReaderTool.voice_patterns
        )
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "прочитай сторінку",
        "прочитай цю сторінку",
        "скопіюй текст сторінки",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in ContentReaderTool.voice_patterns
        )
        assert matched, f"No UK pattern matched: {utterance!r}"


# -- helper functions ---------------------------------------------------


class TestExtractReadable:
    def test_strips_html_tags(self):
        text = _extract_readable("<html><body><p>Hello world</p></body></html>")
        assert "Hello world" in text

    def test_respects_max_chars(self):
        text = _extract_readable("<p>" + "x" * 10000 + "</p>", max_chars=100)
        assert len(text) <= 100


class TestExtractTitle:
    def test_extracts_title(self):
        assert _extract_title("<html><title>My Page</title></html>") == "My Page"

    def test_empty_on_no_title(self):
        assert _extract_title("<html><body>No title</body></html>") == ""


class TestHttpxFetch:
    def test_success(self, monkeypatch):
        pytest.importorskip("httpx")
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>OK</body></html>"
        mock_resp.url = "https://example.com"
        mock_resp.raise_for_status = MagicMock()
        import httpx
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException
        monkeypatch.setitem(__import__("sys").modules, "httpx", mock_httpx)
        result = _httpx_fetch("https://example.com")
        assert result["success"] is True

    def test_connect_error(self, monkeypatch):
        real_httpx = pytest.importorskip("httpx")
        mock_httpx = MagicMock()
        mock_httpx.ConnectError = real_httpx.ConnectError
        mock_httpx.TimeoutException = real_httpx.TimeoutException
        mock_httpx.get.side_effect = real_httpx.ConnectError("no internet")
        monkeypatch.setitem(__import__("sys").modules, "httpx", mock_httpx)
        result = _httpx_fetch("https://example.com")
        assert result["success"] is False
        assert "internet" in result["error"].lower() or "connect" in result["error"].lower()


# -- execute dispatch ---------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_browser(monkeypatch):
    mock_bc = MagicMock()
    mock_bc.driver_active = False
    mock_bc.get_page_text.return_value = {"success": True, "text": "Hello page"}
    mock_bc.get_page_title.return_value = {"success": True, "title": "Test"}
    mock_bc.get_current_url.return_value = {"success": True, "url": "https://example.com"}
    monkeypatch.setattr(
        "jarvis.tools.local.browser_tabs._get_browser",
        lambda: mock_bc,
    )
    return mock_bc


class TestExecute:
    @pytest.mark.asyncio
    async def test_read_page_with_url(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.content_reader._httpx_fetch",
            lambda url, **kw: {"success": True, "html": "<p>Content here</p>"},
        )
        tool = ContentReaderTool()
        result = await tool.execute(ContentReaderArgs(action="read_page", url="https://example.com"))
        assert result.success
        assert "Content here" in result.output

    @pytest.mark.asyncio
    async def test_get_title_with_url(self, monkeypatch):
        monkeypatch.setattr(
            "jarvis.tools.local.content_reader._httpx_fetch",
            lambda url, **kw: {"success": True, "html": "<title>Hello</title>"},
        )
        tool = ContentReaderTool()
        result = await tool.execute(ContentReaderArgs(action="get_title", url="https://example.com"))
        assert result.success
        assert "Hello" in result.output

    @pytest.mark.asyncio
    async def test_read_page_no_url_no_browser(self, _mock_browser):
        _mock_browser.driver_active = False
        tool = ContentReaderTool()
        result = await tool.execute(ContentReaderArgs(action="read_page"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_read_page_with_selenium(self, _mock_browser):
        _mock_browser.driver_active = True
        _mock_browser.get_page_text.return_value = {
            "success": True,
            "text": "Selenium extracted text",
        }
        tool = ContentReaderTool()
        result = await tool.execute(ContentReaderArgs(action="read_page"))
        assert result.success
        assert "Selenium extracted" in result.output
