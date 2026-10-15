"""Look up and compare prices for a product or game across stores.

Constructs search URLs for price comparison sites and scrapes results
using httpx + BeautifulSoup.  No external APIs — everything is done
via web scraping and URL construction.

For games: prioritises IsThereAnyDeal.com and GG.deals.
For general products: uses "{product} price comparison" web search.

Dependencies (all optional, checked at runtime):
    - httpx for HTTP requests
    - beautifulsoup4 for HTML parsing
    - browser_controller (Selenium) for current page URL (optional)

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Heuristic: if the URL or product name contains these, treat as a game.
_GAME_STORE_DOMAINS = frozenset({
    "store.steampowered.com", "steampowered.com",
    "gog.com", "epicgames.com", "humblebundle.com",
    "nintendo.com", "playstation.com", "xbox.com",
})

_GAME_KEYWORDS = re.compile(
    r"\b(steam|gog|epic\s*games?|playstation|xbox|nintendo|dlc|edition)\b",
    re.IGNORECASE,
)


def _is_game_product(product: str, url: str = "") -> bool:
    """Heuristic: decide whether the product is a video game."""
    if url:
        domain = urlparse(url).hostname or ""
        if any(d in domain for d in _GAME_STORE_DOMAINS):
            return True
    return bool(_GAME_KEYWORDS.search(product))


def _build_search_urls(product: str, is_game: bool) -> list[dict[str, str]]:
    """Construct price-comparison search URLs.

    Returns a list of {"source": <name>, "url": <search url>}.
    """
    encoded = quote_plus(product)
    urls: list[dict[str, str]] = []

    if is_game:
        # IsThereAnyDeal search
        urls.append({
            "source": "IsThereAnyDeal",
            "url": f"https://isthereanydeal.com/search/?q={encoded}",
        })
        # GG.deals search
        urls.append({
            "source": "GG.deals",
            "url": f"https://gg.deals/games/?title={encoded}",
        })
    else:
        # General product price comparison via Google Shopping
        urls.append({
            "source": "Google Shopping",
            "url": (
                f"https://www.google.com/search?q={encoded}+price+comparison"
                "&tbm=shop"
            ),
        })
        # PriceGrabber-style general search
        urls.append({
            "source": "Google",
            "url": f"https://www.google.com/search?q={encoded}+buy+price",
        })

    return urls


def _fetch_and_parse(url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch a URL with httpx and return parsed text via BeautifulSoup.

    Returns {"success": True, "text": ..., "title": ...} or
            {"success": False, "error": ...}.
    """
    try:
        import httpx
    except ImportError:
        return {"success": False, "error": "httpx not installed."}

    try:
        import bs4  # noqa: F401
    except ImportError:
        return {"success": False, "error": "beautifulsoup4 not installed."}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
        resp.raise_for_status()
    except httpx.ConnectError:
        return {"success": False, "error": "No internet connection."}
    except httpx.TimeoutException:
        return {"success": False, "error": "Request timed out."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    text = soup.get_text(separator="\n", strip=True)
    return {"success": True, "text": text[:8000], "title": title}


# Price pattern: currency symbol + digits or digits + currency code.
_PRICE_RE = re.compile(
    r"(?:[\$\u20ac\u00a3\u20b4])\s*\d+[\.,]?\d*"
    r"|"
    r"\d+[\.,]?\d*\s*(?:USD|EUR|GBP|UAH|RUB|PLN)",
    re.IGNORECASE,
)


def _extract_prices(text: str) -> list[str]:
    """Extract price-like strings from text."""
    return _PRICE_RE.findall(text)[:20]  # Cap at 20.


def _format_results(
    results: list[dict[str, Any]], product: str
) -> str:
    """Format results from multiple sources into a readable string."""
    if not results:
        return f"No price information found for \"{product}\"."

    lines: list[str] = [f"Price comparison for \"{product}\":\n"]
    for r in results:
        source = r["source"]
        prices = r.get("prices", [])
        url = r.get("url", "")
        if prices:
            price_str = ", ".join(prices[:5])
            lines.append(f"  {source}: {price_str}")
        else:
            lines.append(f"  {source}: no prices found")
        if url:
            lines.append(f"    Link: {url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class PriceLookupArgs(BaseModel):
    product: str = Field(
        default="",
        description=(
            "Product or game name to search for. If empty, attempts to "
            "detect the product from the current browser tab."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class PriceLookupTool:
    """Find where a product or game is cheaper across stores."""

    name: str = "price_lookup"
    description: str = (
        "Searches for the best price of a product or game across stores. "
        "For games, checks IsThereAnyDeal and GG.deals. For products, "
        "searches price comparison sites. Provide a product name or leave "
        "empty to detect from the current browser tab. "
        "(RU: найди дешевле, где это дешевле, сравни цены / "
        "UK: знайди дешевше, де це дешевше, порівняй ціни)"
    )
    args_schema = PriceLookupArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — 4 patterns
        VoicePattern(
            regex=r"^find\s+(?:a\s+)?cheaper\s+price$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^where\s+is\s+(?:this|it)\s+cheaper$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:price\s+)?compar(?:e|ison)(?:\s+prices?)?$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^find\s+(?:the\s+)?best\s+price$",
            priority=470,
            args=lambda m: {},
        ),
        # RU — 4 patterns
        VoicePattern(
            regex=r"^найди\s+дешевле$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^где\s+(?:это\s+)?дешевле$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^сравни\s+цен[ыу]$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^найди\s+лучшую\s+цену$",
            priority=470,
            args=lambda m: {},
        ),
        # UK — 4 patterns
        VoicePattern(
            regex=r"^знайди\s+дешевше$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^де\s+це\s+дешевше$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^порівняй\s+цін[иу]$",
            priority=470,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^знайди\s+найкращу\s+ціну$",
            priority=470,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        browser_controller: Any = None,
    ) -> None:
        self._browser_controller = browser_controller

    async def execute(self, args: PriceLookupArgs) -> ToolResult:
        """Main entry point: determine product → search → return prices."""
        product = args.product.strip()
        page_url = ""

        # If no product name given, try to get it from the browser.
        if not product:
            product, page_url = self._detect_from_browser()

        if not product:
            return ToolResult(
                success=False,
                error=(
                    "No product name provided and no active browser tab "
                    "to detect from."
                ),
            )

        is_game = _is_game_product(product, page_url)
        search_urls = _build_search_urls(product, is_game)

        results = await asyncio.to_thread(
            self._search_prices, search_urls
        )

        output = _format_results(results, product)
        return ToolResult(success=True, output=output)

    def _detect_from_browser(self) -> tuple[str, str]:
        """Try to detect product name from the current browser tab.

        Returns (product_name, page_url).
        """
        bc = self._browser_controller
        if bc is None or not getattr(bc, "driver_active", False):
            return "", ""

        try:
            url_result = bc.get_current_url()
            page_url = url_result.get("url", "")
            title_result = bc.get_page_title()
            page_title = title_result.get("title", "")
            # Use page title as product name; strip common suffixes.
            product = page_title
            for suffix in (" on Steam", " - Steam", " - GOG.com",
                           " - Epic Games Store", " | Amazon",
                           " - Buy", " - Price"):
                if product.endswith(suffix):
                    product = product[: -len(suffix)]
            return product.strip(), page_url
        except Exception:
            return "", ""

    @staticmethod
    def _search_prices(
        search_urls: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Fetch each search URL and extract prices."""
        results: list[dict[str, Any]] = []
        for entry in search_urls:
            source = entry["source"]
            url = entry["url"]
            fetched = _fetch_and_parse(url)
            prices: list[str] = []
            if fetched.get("success"):
                prices = _extract_prices(fetched["text"])
            results.append({
                "source": source,
                "prices": prices,
                "url": url,
            })
        return results
