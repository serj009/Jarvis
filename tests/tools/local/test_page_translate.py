"""Tests for jarvis.tools.local.page_translate — TranslatePageTool.

Fully mocked: no real browser, no real LLM, no real network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis.tools.local.page_translate import (
    TranslatePageArgs,
    TranslatePageTool,
    _normalize_language,
    _split_chunks,
    _translate_chunk,
)

FAKE_PAGE_TEXT = "Hello, this is a test page.\nIt has some content.\nEnd of page."
FAKE_TRANSLATION = "Привет, это тестовая страница.\nУ неё есть контент.\nКонец страницы."


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class FakeChunk:
    def __init__(self, content: str = "", done: bool = False):
        self.content = content
        self.done = done


def _build_mock_ollama(response: str):
    mock = MagicMock()

    async def fake_stream_chat(messages, **kwargs):
        yield FakeChunk(content=response, done=True)

    mock.stream_chat = fake_stream_chat
    return mock


def _build_mock_browser(text: str, url: str = "https://test.com", active: bool = True):
    bc = MagicMock()
    bc.driver_active = active
    bc.get_page_text.return_value = {"success": True, "text": text}
    bc.get_current_url.return_value = {"success": True, "url": url}
    return bc


# ---------------------------------------------------------------------------
# Unit tests — _normalize_language
# ---------------------------------------------------------------------------

def test_normalize_english():
    assert _normalize_language("english") == "English"
    assert _normalize_language("English") == "English"


def test_normalize_russian_ru():
    assert _normalize_language("русский") == "Russian"


def test_normalize_ukrainian_uk():
    assert _normalize_language("українська") == "Ukrainian"


def test_normalize_unknown():
    assert _normalize_language("klingon") == "Klingon"


def test_normalize_english_from_ru():
    assert _normalize_language("английский") == "English"


def test_normalize_english_from_uk():
    assert _normalize_language("англійська") == "English"


# ---------------------------------------------------------------------------
# Unit tests — _split_chunks
# ---------------------------------------------------------------------------

def test_split_short_text():
    chunks = _split_chunks("short text", chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0] == "short text"


def test_split_long_text():
    text = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    chunks = _split_chunks(text, chunk_size=15)
    assert len(chunks) >= 2
    # All text preserved.
    # The original content should be present across chunks.
    assert "Line 1" in "".join(chunks)
    assert "Line 5" in "".join(chunks)


def test_split_no_good_newline():
    # One very long line with no newlines.
    text = "A" * 100
    chunks = _split_chunks(text, chunk_size=30)
    assert len(chunks) >= 3
    assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# Unit test — _translate_chunk
# ---------------------------------------------------------------------------

async def test_translate_chunk():
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    result = await _translate_chunk(ollama, FAKE_PAGE_TEXT, "Russian")
    assert result == FAKE_TRANSLATION


# ---------------------------------------------------------------------------
# Tool-level async tests
# ---------------------------------------------------------------------------

async def test_translate_no_llm():
    tool = TranslatePageTool(ollama_client=None)
    result = await tool.execute(TranslatePageArgs(url="https://test.com"))
    assert not result.success
    assert "LLM" in (result.error or "")


async def test_translate_no_url_no_browser():
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    tool = TranslatePageTool(ollama_client=ollama, browser_controller=None)
    result = await tool.execute(TranslatePageArgs(url="", target_language="Russian"))
    assert not result.success
    assert "url" in (result.error or "").lower() or "browser" in (result.error or "").lower()


async def test_translate_from_browser():
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    bc = _build_mock_browser(FAKE_PAGE_TEXT)
    tool = TranslatePageTool(ollama_client=ollama, browser_controller=bc)
    result = await tool.execute(TranslatePageArgs(url="", target_language="Russian"))
    assert result.success
    assert "Привет" in (result.output or "") or "Translation" in (result.output or "")


@patch("jarvis.tools.local.page_translate._get_page_text")
async def test_translate_from_url(mock_get_page):
    mock_get_page.return_value = (FAKE_PAGE_TEXT, "https://test.com", None)
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    tool = TranslatePageTool(ollama_client=ollama)
    result = await tool.execute(
        TranslatePageArgs(url="https://test.com", target_language="Russian")
    )
    assert result.success


@patch("jarvis.tools.local.page_translate._get_page_text")
async def test_translate_empty_page(mock_get_page):
    mock_get_page.return_value = ("", "https://test.com", None)
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    tool = TranslatePageTool(ollama_client=ollama)
    result = await tool.execute(TranslatePageArgs(url="https://test.com"))
    assert not result.success
    assert "empty" in (result.error or "").lower()


@patch("jarvis.tools.local.page_translate._get_page_text")
async def test_translate_fetch_error(mock_get_page):
    mock_get_page.return_value = ("", "https://test.com", "Connection refused")
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    tool = TranslatePageTool(ollama_client=ollama)
    result = await tool.execute(TranslatePageArgs(url="https://test.com"))
    assert not result.success
    assert "Connection" in (result.error or "")


@patch("jarvis.tools.local.page_translate._get_page_text")
async def test_translate_language_normalization(mock_get_page):
    mock_get_page.return_value = (FAKE_PAGE_TEXT, "https://test.com", None)
    ollama = _build_mock_ollama(FAKE_TRANSLATION)
    tool = TranslatePageTool(ollama_client=ollama)
    result = await tool.execute(
        TranslatePageArgs(url="https://test.com", target_language="русский")
    )
    assert result.success
    assert "Russian" in (result.output or "")


# ---------------------------------------------------------------------------
# Voice patterns
# ---------------------------------------------------------------------------

def test_has_voice_patterns():
    patterns = TranslatePageTool.voice_patterns
    assert len(patterns) >= 12  # 4 EN + 4 RU + 4 UK


def test_requires_confirmation_false():
    assert TranslatePageTool().requires_confirmation is False
