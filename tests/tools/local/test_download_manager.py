"""Tests for jarvis.tools.local.download_manager.

All tests mock requests to avoid real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis.tools.local.download_manager import (
    DownloadArgs,
    DownloadFileTool,
    _filename_from_url,
)

# --- Unit tests ------------------------------------------------------------

def test_filename_from_url_basic():
    assert _filename_from_url("https://example.com/file.zip") == "file.zip"


def test_filename_from_url_with_query():
    assert _filename_from_url("https://example.com/file.zip?v=1") == "file.zip"


def test_filename_from_url_no_extension():
    assert _filename_from_url("https://example.com/download") == "download"


def test_filename_from_url_empty():
    assert _filename_from_url("https://example.com/") == "download"


# --- Tool execution tests --------------------------------------------------

async def test_download_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-length": "1024"}
    mock_response.iter_content.return_value = [b"x" * 1024]
    mock_response.raise_for_status = MagicMock()

    with patch("jarvis.tools.local.download_manager._download_file") as mock_dl:
        mock_dl.return_value = {
            "success": True,
            "path": "/tmp/test.zip",
            "filename": "test.zip",
            "size_bytes": 1024,
            "elapsed_sec": 0.5,
        }
        result = await DownloadFileTool().execute(
            DownloadArgs(url="https://example.com/test.zip"),
        )
    assert result.success
    assert "test.zip" in (result.output or "")


async def test_download_failure():
    with patch("jarvis.tools.local.download_manager._download_file") as mock_dl:
        mock_dl.return_value = {
            "success": False,
            "error": "404 Not Found",
        }
        result = await DownloadFileTool().execute(
            DownloadArgs(url="https://example.com/missing.zip"),
        )
    assert not result.success
    assert "404" in (result.error or "")


async def test_download_with_custom_filename():
    with patch("jarvis.tools.local.download_manager._download_file") as mock_dl:
        mock_dl.return_value = {
            "success": True,
            "path": "/tmp/custom.zip",
            "filename": "custom.zip",
            "size_bytes": 2048,
            "elapsed_sec": 1.0,
        }
        result = await DownloadFileTool().execute(
            DownloadArgs(url="https://example.com/file.zip", filename="custom.zip"),
        )
    assert result.success
    assert "custom.zip" in (result.output or "")


async def test_download_exception():
    with patch(
        "jarvis.tools.local.download_manager._download_file",
        side_effect=RuntimeError("network error"),
    ):
        result = await DownloadFileTool().execute(
            DownloadArgs(url="https://example.com/file.zip"),
        )
    assert not result.success
    assert "failed" in (result.error or "").lower()


def test_requires_confirmation_false():
    assert DownloadFileTool().requires_confirmation is False
