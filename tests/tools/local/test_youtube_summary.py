"""Tests for jarvis.tools.local.youtube_summary.

All tests mock yt-dlp and LLM client to avoid real network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from jarvis.tools.local.youtube_summary import (
    YoutubeSummaryArgs,
    YoutubeSummaryTool,
    _clean_subtitle_text,
    _extract_video_id,
    _is_youtube_url,
)

# --- Unit tests: URL detection --------------------------------------------

def test_is_youtube_url_standard():
    assert _is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_is_youtube_url_short():
    assert _is_youtube_url("https://youtu.be/dQw4w9WgXcQ")


def test_is_youtube_url_no_scheme():
    assert _is_youtube_url("youtube.com/watch?v=dQw4w9WgXcQ")


def test_is_not_youtube_url():
    assert not _is_youtube_url("https://example.com/video")


def test_extract_video_id():
    assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_short():
    assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_invalid():
    assert _extract_video_id("https://example.com") == ""


# --- Unit tests: subtitle cleaning ----------------------------------------

def test_clean_subtitle_text_vtt():
    raw = (
        "WEBVTT\nKind: captions\n\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "Hello world\n\n"
        "00:00:04.000 --> 00:00:07.000\n"
        "This is a test\n\n"
        "00:00:07.000 --> 00:00:10.000\n"
        "This is a test\n"  # duplicate
    )
    cleaned = _clean_subtitle_text(raw)
    assert "Hello world" in cleaned
    assert "This is a test" in cleaned
    # Timestamps should be removed.
    assert "-->" not in cleaned


def test_clean_subtitle_text_removes_tags():
    raw = (
        "00:00:01.000 --> 00:00:04.000\n"
        "<b>Bold text</b> and <i>italic</i>\n"
    )
    cleaned = _clean_subtitle_text(raw)
    assert "Bold text" in cleaned
    assert "<b>" not in cleaned


def test_clean_subtitle_text_empty():
    assert _clean_subtitle_text("") == ""


# --- Tool execution tests -------------------------------------------------

async def test_execute_no_llm():
    """Should fail if no LLM client is provided."""
    tool = YoutubeSummaryTool(ollama_client=None)
    result = await tool.execute(YoutubeSummaryArgs(url="https://youtu.be/abc12345678"))
    assert not result.success
    assert "LLM" in (result.error or "")


async def test_execute_no_url_no_browser():
    """Should fail if no URL and no browser."""
    mock_llm = MagicMock()
    tool = YoutubeSummaryTool(ollama_client=mock_llm, browser_controller=None)
    result = await tool.execute(YoutubeSummaryArgs())
    assert not result.success
    assert "YouTube URL" in (result.error or "")


async def test_execute_non_youtube_url():
    """Non-YouTube URL should fail."""
    mock_llm = MagicMock()
    tool = YoutubeSummaryTool(ollama_client=mock_llm)
    result = await tool.execute(
        YoutubeSummaryArgs(url="https://example.com/video")
    )
    assert not result.success


async def test_execute_success():
    """Successful subtitle download + LLM summarisation."""
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value={
        "message": {"content": "- Key point 1\n- Key point 2\n- Key point 3"}
    })

    sub_result = {
        "success": True,
        "text": "Hello, welcome to this video about Python programming.",
        "title": "Learn Python in 10 Minutes",
    }

    with patch(
        "jarvis.tools.local.youtube_summary._download_subtitles",
        return_value=sub_result,
    ):
        tool = YoutubeSummaryTool(ollama_client=mock_llm)
        result = await tool.execute(
            YoutubeSummaryArgs(url="https://youtu.be/abc12345678")
        )

    assert result.success
    assert "Key point" in (result.output or "")
    assert "Learn Python" in (result.output or "")


async def test_execute_no_subtitles():
    """Should return error when subtitles are unavailable."""
    mock_llm = MagicMock()
    sub_result = {
        "success": False,
        "error": "No subtitles or description available for this video.",
    }

    with patch(
        "jarvis.tools.local.youtube_summary._download_subtitles",
        return_value=sub_result,
    ):
        tool = YoutubeSummaryTool(ollama_client=mock_llm)
        result = await tool.execute(
            YoutubeSummaryArgs(url="https://youtu.be/abc12345678")
        )

    assert not result.success
    assert "subtitles" in (result.error or "").lower()


async def test_execute_detects_from_browser():
    """Browser controller should be used when no URL is given."""
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value={
        "message": {"content": "Summary of the video."}
    })
    mock_bc = MagicMock()
    mock_bc.driver_active = True
    mock_bc.get_current_url.return_value = {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    }

    sub_result = {
        "success": True,
        "text": "Never gonna give you up...",
        "title": "Rick Astley - Never Gonna Give You Up",
    }

    with patch(
        "jarvis.tools.local.youtube_summary._download_subtitles",
        return_value=sub_result,
    ):
        tool = YoutubeSummaryTool(
            ollama_client=mock_llm, browser_controller=mock_bc
        )
        result = await tool.execute(YoutubeSummaryArgs())

    assert result.success


async def test_execute_llm_failure():
    """LLM failure should be handled."""
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    sub_result = {
        "success": True,
        "text": "Video content here.",
        "title": "Test Video",
    }

    with patch(
        "jarvis.tools.local.youtube_summary._download_subtitles",
        return_value=sub_result,
    ):
        tool = YoutubeSummaryTool(ollama_client=mock_llm)
        result = await tool.execute(
            YoutubeSummaryArgs(url="https://youtu.be/abc12345678")
        )

    assert not result.success
    assert "failed" in (result.error or "").lower()


def test_requires_confirmation_false():
    assert YoutubeSummaryTool(ollama_client=None).requires_confirmation is False


def test_voice_patterns_count():
    """Must have at least 12 voice patterns (4 EN + 4 RU + 4 UK)."""
    assert len(YoutubeSummaryTool.voice_patterns) >= 12
