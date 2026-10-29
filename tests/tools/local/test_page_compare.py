"""Tests for jarvis.tools.local.page_compare — ComparePagesTool.

Fully mocked: no real browser, no real LLM, no real network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis.tools.local.page_compare import (
    ComparePageArgs,
    ComparePagesTool,
    _collect_text,
    _llm_compare,
)

FAKE_PAGE_1 = "Product A\nPrice: $100\nRAM: 8GB\nScreen: 15 inch"
FAKE_PAGE_2 = "Product B\nPrice: $200\nRAM: 16GB\nScreen: 14 inch"

FAKE_COMPARISON = """\
| Feature | Product A | Product B |
|---------|-----------|-----------|
| Price   | $100      | $200      |
| RAM     | 8GB       | 16GB      |
| Screen  | 15 inch   | 14 inch   |
"""


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


def _build_mock_browser(text: str, active: bool = True):
    mock = MagicMock()
    mock.driver_active = active
    mock.get_page_text.return_value = {"success": True, "text": text}
    return mock


# ---------------------------------------------------------------------------
# Unit tests — _collect_text
# ---------------------------------------------------------------------------

async def test_collect_text_current_tab():
    bc = _build_mock_browser(FAKE_PAGE_1)
    text, err = await _collect_text("current", bc)
    assert err is None
    assert text == FAKE_PAGE_1


async def test_collect_text_no_browser():
    text, err = await _collect_text("current", None)
    assert err is not None
    assert "browser" in err.lower()


@patch("jarvis.tools.local.page_compare._fetch_text")
async def test_collect_text_url(mock_fetch):
    mock_fetch.return_value = (FAKE_PAGE_2, "https://example.com", None)
    text, err = await _collect_text("https://example.com", None)
    assert err is None
    assert text == FAKE_PAGE_2


@patch("jarvis.tools.local.page_compare._fetch_text")
async def test_collect_text_url_error(mock_fetch):
    mock_fetch.return_value = ("", "https://example.com", "Fetch failed")
    text, err = await _collect_text("https://example.com", None)
    assert err is not None


# ---------------------------------------------------------------------------
# Unit test — _llm_compare
# ---------------------------------------------------------------------------

async def test_llm_compare():
    ollama = _build_mock_ollama(FAKE_COMPARISON)
    result = await _llm_compare(ollama, FAKE_PAGE_1, FAKE_PAGE_2, "url1", "url2")
    assert "Product A" in result or "Feature" in result


# ---------------------------------------------------------------------------
# Tool-level async tests
# ---------------------------------------------------------------------------

@patch("jarvis.tools.local.page_compare._fetch_text")
async def test_compare_pages_success(mock_fetch):
    mock_fetch.side_effect = [
        (FAKE_PAGE_1, "https://a.com", None),
        (FAKE_PAGE_2, "https://b.com", None),
    ]
    ollama = _build_mock_ollama(FAKE_COMPARISON)
    tool = ComparePagesTool(ollama_client=ollama)
    result = await tool.execute(
        ComparePageArgs(url1="https://a.com", url2="https://b.com")
    )
    assert result.success
    assert result.output  # non-empty comparison


async def test_compare_pages_no_llm():
    tool = ComparePagesTool(ollama_client=None)
    result = await tool.execute(
        ComparePageArgs(url1="https://a.com", url2="https://b.com")
    )
    assert not result.success
    assert "LLM" in (result.error or "")


@patch("jarvis.tools.local.page_compare._fetch_text")
async def test_compare_pages_page1_error(mock_fetch):
    mock_fetch.side_effect = [
        ("", "https://a.com", "Connection refused"),
        (FAKE_PAGE_2, "https://b.com", None),
    ]
    ollama = _build_mock_ollama(FAKE_COMPARISON)
    tool = ComparePagesTool(ollama_client=ollama)
    result = await tool.execute(
        ComparePageArgs(url1="https://a.com", url2="https://b.com")
    )
    assert not result.success
    assert "Page 1" in (result.error or "")


@patch("jarvis.tools.local.page_compare._fetch_text")
async def test_compare_pages_empty_page(mock_fetch):
    mock_fetch.side_effect = [
        (FAKE_PAGE_1, "https://a.com", None),
        ("", "https://b.com", None),
    ]
    ollama = _build_mock_ollama(FAKE_COMPARISON)
    tool = ComparePagesTool(ollama_client=ollama)
    result = await tool.execute(
        ComparePageArgs(url1="https://a.com", url2="https://b.com")
    )
    assert not result.success
    assert "empty" in (result.error or "").lower()


async def test_compare_with_browser_current():
    bc = _build_mock_browser(FAKE_PAGE_1)
    ollama = _build_mock_ollama(FAKE_COMPARISON)

    # url2 must be fetched via httpx
    with patch("jarvis.tools.local.page_compare._fetch_text") as mock_fetch:
        mock_fetch.return_value = (FAKE_PAGE_2, "https://b.com", None)
        tool = ComparePagesTool(ollama_client=ollama, browser_controller=bc)
        result = await tool.execute(
            ComparePageArgs(url1="current", url2="https://b.com")
        )
    assert result.success


# ---------------------------------------------------------------------------
# Voice patterns
# ---------------------------------------------------------------------------

def test_has_voice_patterns():
    patterns = ComparePagesTool.voice_patterns
    assert len(patterns) >= 9  # 3 EN + 3 RU + 3 UK


def test_requires_confirmation_false():
    assert ComparePagesTool().requires_confirmation is False
