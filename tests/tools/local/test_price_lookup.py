"""Tests for jarvis.tools.local.price_lookup.

All tests mock httpx to avoid real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.price_lookup import (
    PriceLookupArgs,
    PriceLookupTool,
    _build_search_urls,
    _extract_prices,
    _format_results,
    _is_game_product,
)

# --- Unit tests: game detection -------------------------------------------

def test_is_game_product_steam_url():
    assert _is_game_product("Elden Ring", "https://store.steampowered.com/app/1245620")


def test_is_game_product_keyword():
    assert _is_game_product("Cyberpunk 2077 Steam Edition")


def test_is_not_game_product():
    assert not _is_game_product("Samsung Galaxy S24")


def test_is_game_product_gog_url():
    assert _is_game_product("The Witcher 3", "https://www.gog.com/game/the_witcher_3")


# --- Unit tests: URL construction -----------------------------------------

def test_build_search_urls_game():
    urls = _build_search_urls("Elden Ring", is_game=True)
    assert len(urls) == 2
    sources = {u["source"] for u in urls}
    assert "IsThereAnyDeal" in sources
    assert "GG.deals" in sources
    # Check encoding.
    for u in urls:
        assert "Elden+Ring" in u["url"] or "Elden%20Ring" in u["url"]


def test_build_search_urls_product():
    urls = _build_search_urls("iPhone 15 Pro", is_game=False)
    assert len(urls) == 2
    sources = {u["source"] for u in urls}
    assert "Google Shopping" in sources


# --- Unit tests: price extraction -----------------------------------------

def test_extract_prices_dollar():
    text = "The game costs $59.99 on Steam and $49.99 on GOG."
    prices = _extract_prices(text)
    assert "$59.99" in prices
    assert "$49.99" in prices


def test_extract_prices_euro():
    text = "Price: €39.99 on sale"
    prices = _extract_prices(text)
    assert len(prices) >= 1
    assert "€39.99" in prices


def test_extract_prices_empty():
    text = "No pricing information here."
    prices = _extract_prices(text)
    assert prices == []


def test_extract_prices_usd_suffix():
    text = "Only 29.99 USD available"
    prices = _extract_prices(text)
    assert len(prices) >= 1


# --- Unit tests: result formatting ----------------------------------------

def test_format_results_with_prices():
    results = [
        {"source": "Steam", "prices": ["$59.99"], "url": "https://store.steampowered.com"},
        {"source": "GOG", "prices": ["$49.99"], "url": "https://www.gog.com"},
    ]
    output = _format_results(results, "Test Game")
    assert "Test Game" in output
    assert "$59.99" in output
    assert "GOG" in output


def test_format_results_empty():
    output = _format_results([], "Test Product")
    assert "No price information" in output


# --- Tool execution tests -------------------------------------------------

async def test_execute_with_product_name():
    """Providing a product name should search and return results."""
    fake_result = {
        "success": True,
        "text": "Game on sale for $29.99 at Store A. Regular price $59.99.",
        "title": "Price results",
    }
    with patch(
        "jarvis.tools.local.price_lookup._fetch_and_parse",
        return_value=fake_result,
    ):
        tool = PriceLookupTool()
        result = await tool.execute(PriceLookupArgs(product="Elden Ring"))

    assert result.success
    assert "Elden Ring" in (result.output or "")


async def test_execute_no_product_no_browser():
    """No product and no browser should return an error."""
    tool = PriceLookupTool(browser_controller=None)
    result = await tool.execute(PriceLookupArgs())
    assert not result.success
    assert "No product name" in (result.error or "")


async def test_execute_detects_from_browser():
    """Browser controller should be used when no product is given."""
    mock_bc = MagicMock()
    mock_bc.driver_active = True
    mock_bc.get_current_url.return_value = {
        "url": "https://store.steampowered.com/app/1245620/Elden_Ring/"
    }
    mock_bc.get_page_title.return_value = {"title": "Elden Ring on Steam"}

    fake_result = {
        "success": True,
        "text": "Price: $59.99",
        "title": "Results",
    }
    with patch(
        "jarvis.tools.local.price_lookup._fetch_and_parse",
        return_value=fake_result,
    ):
        tool = PriceLookupTool(browser_controller=mock_bc)
        result = await tool.execute(PriceLookupArgs())

    assert result.success
    assert "Elden Ring" in (result.output or "")


async def test_execute_network_failure():
    """Network failure should be handled gracefully."""
    fake_result = {
        "success": False,
        "error": "No internet connection.",
    }
    with patch(
        "jarvis.tools.local.price_lookup._fetch_and_parse",
        return_value=fake_result,
    ):
        tool = PriceLookupTool()
        result = await tool.execute(PriceLookupArgs(product="Test Product"))

    # Tool still succeeds (returns "no prices found" rather than failing).
    assert result.success
    assert "no prices found" in (result.output or "").lower()


def test_requires_confirmation_false():
    assert PriceLookupTool().requires_confirmation is False


def test_voice_patterns_count():
    """Must have at least 12 voice patterns (4 EN + 4 RU + 4 UK)."""
    assert len(PriceLookupTool.voice_patterns) >= 12
