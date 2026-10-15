"""Tests for jarvis.tools.local.image_scraper.

All tests mock httpx to avoid real network calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.image_scraper import (
    ScrapeImagesArgs,
    ScrapeImagesTool,
    _extract_image_urls,
    _is_tracking_pixel,
    _parse_dimension,
    _safe_filename,
)


# --- Unit tests: image URL extraction -------------------------------------

def test_extract_image_urls_basic():
    html = """
    <html><body>
        <img src="/images/photo1.jpg" />
        <img src="https://cdn.example.com/photo2.png" />
    </body></html>
    """
    urls = _extract_image_urls(html, "https://example.com")
    assert len(urls) == 2
    assert "https://example.com/images/photo1.jpg" in urls
    assert "https://cdn.example.com/photo2.png" in urls


def test_extract_image_urls_skips_base64():
    html = """
    <img src="data:image/png;base64,iVBORw0KGgo=" />
    <img src="/real_image.jpg" />
    """
    urls = _extract_image_urls(html, "https://example.com")
    assert len(urls) == 1
    assert "real_image.jpg" in urls[0]


def test_extract_image_urls_skips_tiny_images():
    html = """
    <img src="/icon.png" width="16" height="16" />
    <img src="/photo.jpg" width="800" height="600" />
    """
    urls = _extract_image_urls(html, "https://example.com")
    assert len(urls) == 1
    assert "photo.jpg" in urls[0]


def test_extract_image_urls_srcset():
    html = """
    <img src="/small.jpg" srcset="/medium.jpg 2x, /large.jpg 3x" />
    """
    urls = _extract_image_urls(html, "https://example.com")
    # Should include src + srcset variants (deduplicated).
    assert len(urls) >= 2


def test_extract_image_urls_deduplicates():
    html = """
    <img src="/photo.jpg" />
    <img src="/photo.jpg" />
    """
    urls = _extract_image_urls(html, "https://example.com")
    assert len(urls) == 1


def test_extract_image_urls_empty_html():
    urls = _extract_image_urls("", "https://example.com")
    assert urls == []


# --- Unit tests: helpers ---------------------------------------------------

def test_parse_dimension_valid():
    assert _parse_dimension("800") == 800
    assert _parse_dimension("100px") == 100


def test_parse_dimension_invalid():
    assert _parse_dimension("") is None
    assert _parse_dimension("auto") is None


def test_is_tracking_pixel():
    assert _is_tracking_pixel("https://example.com/pixel.gif")
    assert _is_tracking_pixel("https://analytics.example.com/beacon")
    assert not _is_tracking_pixel("https://cdn.example.com/photo.jpg")


def test_safe_filename():
    name = _safe_filename("https://example.com/images/photo.jpg", 0)
    assert name == "photo.jpg"


def test_safe_filename_no_extension():
    name = _safe_filename("https://example.com/download", 5)
    assert name == "image_005.jpg"


def test_safe_filename_long_name():
    long_name = "a" * 200 + ".jpg"
    name = _safe_filename(f"https://example.com/{long_name}", 0)
    assert len(name) <= 100


# --- Tool execution tests -------------------------------------------------

async def test_execute_success():
    """Successful image scraping."""
    html = """
    <html><body>
        <img src="https://cdn.example.com/photo1.jpg" />
        <img src="https://cdn.example.com/photo2.png" />
    </body></html>
    """

    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=(html, "https://example.com", None),
    ), patch(
        "jarvis.tools.local.image_scraper._download_images",
        return_value={
            "downloaded": 2,
            "failed": 0,
            "skipped": 0,
            "dir": "/tmp/images_example_com_20240101",
        },
    ):
        tool = ScrapeImagesTool()
        result = await tool.execute(ScrapeImagesArgs(url="https://example.com"))

    assert result.success
    assert "2" in (result.output or "")


async def test_execute_no_images():
    """Page with no images."""
    html = "<html><body><p>No images here.</p></body></html>"

    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=(html, "https://example.com", None),
    ):
        tool = ScrapeImagesTool()
        result = await tool.execute(ScrapeImagesArgs(url="https://example.com"))

    assert result.success
    assert "No downloadable images" in (result.output or "")


async def test_execute_no_url_no_browser():
    """No URL and no browser should fail."""
    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=("", "", "No URL provided and no active browser tab."),
    ):
        tool = ScrapeImagesTool(browser_controller=None)
        result = await tool.execute(ScrapeImagesArgs())

    assert not result.success
    assert "No URL" in (result.error or "")


async def test_execute_network_error():
    """Network error should be handled gracefully."""
    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=("", "", "No internet connection."),
    ):
        tool = ScrapeImagesTool()
        result = await tool.execute(ScrapeImagesArgs(url="https://example.com"))

    assert not result.success
    assert "internet" in (result.error or "").lower()


async def test_execute_with_browser():
    """Browser controller used when no URL given."""
    html = '<html><body><img src="/photo.jpg" /></body></html>'

    mock_bc = MagicMock()
    mock_bc.driver_active = True
    mock_bc.get_page_source.return_value = {"success": True, "html": html}
    mock_bc.get_current_url.return_value = {"url": "https://example.com"}

    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=(html, "https://example.com", None),
    ), patch(
        "jarvis.tools.local.image_scraper._download_images",
        return_value={
            "downloaded": 1,
            "failed": 0,
            "skipped": 0,
            "dir": "/tmp/images",
        },
    ):
        tool = ScrapeImagesTool(browser_controller=mock_bc)
        result = await tool.execute(ScrapeImagesArgs())

    assert result.success


async def test_execute_some_downloads_fail():
    """Partial download failures should be reported."""
    html = """
    <html><body>
        <img src="/photo1.jpg" />
        <img src="/photo2.jpg" />
        <img src="/photo3.jpg" />
    </body></html>
    """

    with patch(
        "jarvis.tools.local.image_scraper._get_page_html",
        return_value=(html, "https://example.com", None),
    ), patch(
        "jarvis.tools.local.image_scraper._download_images",
        return_value={
            "downloaded": 2,
            "failed": 1,
            "skipped": 0,
            "dir": "/tmp/images",
        },
    ):
        tool = ScrapeImagesTool()
        result = await tool.execute(ScrapeImagesArgs(url="https://example.com"))

    assert result.success
    assert "2" in (result.output or "")
    assert "failed" in (result.output or "").lower()


def test_requires_confirmation_false():
    assert ScrapeImagesTool().requires_confirmation is False


def test_voice_patterns_count():
    """Must have at least 9 voice patterns (3 EN + 3 RU + 3 UK)."""
    assert len(ScrapeImagesTool.voice_patterns) >= 9
