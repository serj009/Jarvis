"""RSS/News reader — subscribe to feeds, list subscriptions, fetch news.

Stores subscriptions in ``~/.jarvis/data/rss_feeds.json``.
Parses RSS 2.0 and Atom feeds using ``xml.etree.ElementTree`` (stdlib).
Downloads feeds with ``httpx`` (lazy import, offline-safe).

Tools:
- subscribe_rss: subscribe to an RSS/Atom feed URL
- unsubscribe_rss: remove a subscription by name or URL
- list_rss_feeds: show all current subscriptions
- get_latest_news: fetch and display recent items from subscriptions
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timezone
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.platform.encrypted_store import EncryptedJsonStore
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_RSS_STORE: EncryptedJsonStore | None = None


def _store() -> EncryptedJsonStore:
    # Lazily build the store so the data dir is resolved at call time via the
    # single resolver (jarvis.paths); layout unchanged: <data_dir>/data/rss_feeds.json.
    global _RSS_STORE
    if _RSS_STORE is None:
        from jarvis.paths import data_path

        _RSS_STORE = EncryptedJsonStore(data_path("data", "rss_feeds.json"))
    return _RSS_STORE


def _load_feeds() -> list[dict[str, Any]]:
    """Load feed subscriptions from disk."""
    return _store().load(default=[])


def _save_feeds(feeds: list[dict[str, Any]]) -> None:
    _store().save(feeds)


# ---------------------------------------------------------------------------
# RSS/Atom parser (stdlib xml.etree)
# ---------------------------------------------------------------------------

def _parse_feed_xml(xml_text: str) -> list[dict[str, str]]:
    """Parse RSS 2.0 or Atom XML and return a list of items.

    Each item dict has: title, link, published, summary.
    """
    import xml.etree.ElementTree as ET

    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # Atom namespace
    atom_ns = "{http://www.w3.org/2005/Atom}"

    # Try RSS 2.0 first (root/channel/item)
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (
                    item.findtext("pubDate")
                    or item.findtext("dc:date")
                    or ""
                ).strip(),
                "summary": (item.findtext("description") or "")[:300].strip(),
            })
        return items

    # Try Atom (root is <feed>, entries are <entry>)
    for entry in root.findall(f"{atom_ns}entry"):
        link_el = entry.find(f"{atom_ns}link")
        link = ""
        if link_el is not None:
            link = link_el.get("href", "")
        items.append({
            "title": (entry.findtext(f"{atom_ns}title") or "").strip(),
            "link": link.strip(),
            "published": (
                entry.findtext(f"{atom_ns}published")
                or entry.findtext(f"{atom_ns}updated")
                or ""
            ).strip(),
            "summary": (entry.findtext(f"{atom_ns}summary") or "")[:300].strip(),
        })

    return items


def _fetch_feed(url: str, timeout: float = 15.0) -> tuple[str | None, str | None]:
    """Fetch feed XML. Returns (xml_text, error)."""
    try:
        import httpx
    except ImportError:
        return None, "httpx is not installed."
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text, None
    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class SubscribeRssArgs(BaseModel):
    url: str = Field(description="RSS or Atom feed URL to subscribe to.")
    name: str = Field(
        default="",
        description="Display name for the feed. Auto-detected if empty.",
    )


class UnsubscribeRssArgs(BaseModel):
    name: str = Field(
        default="",
        description="Feed name or URL to unsubscribe from.",
    )


class ListRssFeedsArgs(BaseModel):
    """No arguments needed."""
    pass


class GetLatestNewsArgs(BaseModel):
    count: int = Field(
        default=5,
        description="Maximum number of news items to show per feed.",
        ge=1,
        le=20,
    )
    feed_name: str = Field(
        default="",
        description="Filter by feed name. If empty, shows news from all feeds.",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class SubscribeRssTool:
    name: str = "subscribe_rss"
    description: str = (
        "Subscribes to an RSS or Atom feed URL. Stores the subscription "
        "locally for future news retrieval. "
        "(RU: подпишись на этот сайт, подписка на RSS / "
        "UK: підпишись на цей сайт, підписка на RSS)"
    )
    args_schema = SubscribeRssArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^subscribe\s+(?:to\s+)?(?:this\s+)?(?:rss\s+)?(?:feed\s+)?(?:site\s+)?(.+)?$",
            priority=452,
            args=lambda m: {"url": (m.group(1) or "").strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^подпишись\s+на\s+(?:этот\s+)?(?:сайт|ленту|rss)(?:\s+(.+))?$",
            priority=452,
            args=lambda m: {"url": (m.group(1) or "").strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^підпишись\s+на\s+(?:цей\s+)?(?:сайт|стрічку|rss)(?:\s+(.+))?$",
            priority=452,
            args=lambda m: {"url": (m.group(1) or "").strip()},
        ),
    )

    async def execute(self, args: SubscribeRssArgs) -> ToolResult:
        if not args.url:
            return ToolResult(success=False, error="Feed URL is required.")

        url = args.url.strip()
        # Try to fetch and validate the feed
        xml_text, err = await asyncio.to_thread(_fetch_feed, url)
        if err:
            return ToolResult(
                success=False,
                error=f"Could not fetch feed: {err}",
            )

        items = _parse_feed_xml(xml_text or "")
        name = args.name or url[:80]

        # Try to extract feed title from XML
        if not args.name and xml_text:
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_text)
                channel = root.find("channel")
                if channel is not None:
                    title = channel.findtext("title")
                    if title:
                        name = title.strip()
                else:
                    atom_ns = "{http://www.w3.org/2005/Atom}"
                    title = root.findtext(f"{atom_ns}title")
                    if title:
                        name = title.strip()
            except ET.ParseError:
                pass

        def _do_save() -> None:
            feeds = _load_feeds()
            # Deduplicate by URL
            feeds = [f for f in feeds if f.get("url") != url]
            feeds.append({
                "name": name,
                "url": url,
                "added_at": datetime.now(UTC).isoformat(),
            })
            _save_feeds(feeds)

        await asyncio.to_thread(_do_save)
        return ToolResult(
            success=True,
            output=f"Subscribed to \"{name}\" ({url}). Found {len(items)} items.",
        )


class UnsubscribeRssTool:
    name: str = "unsubscribe_rss"
    description: str = (
        "Removes an RSS/Atom feed subscription by name or URL. "
        "(RU: отпишись от ленты / UK: відпишись від стрічки)"
    )
    args_schema = UnsubscribeRssArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^unsubscribe\s+(?:from\s+)?(.+)$",
            priority=452,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^отпишись\s+(?:от\s+)?(.+)$",
            priority=452,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^відпишись\s+(?:від\s+)?(.+)$",
            priority=452,
            args=lambda m: {"name": m.group(1).strip()},
        ),
    )

    async def execute(self, args: UnsubscribeRssArgs) -> ToolResult:
        if not args.name:
            return ToolResult(success=False, error="Feed name or URL required.")

        query = args.name.lower()

        def _do_remove() -> tuple[bool, str]:
            feeds = _load_feeds()
            before = len(feeds)
            feeds = [
                f for f in feeds
                if query not in f.get("name", "").lower()
                and query not in f.get("url", "").lower()
            ]
            after = len(feeds)
            if before == after:
                return False, f"No feed matching \"{args.name}\" found."
            _save_feeds(feeds)
            return True, f"Unsubscribed from {before - after} feed(s)."

        ok, msg = await asyncio.to_thread(_do_remove)
        return ToolResult(success=ok, output=msg if ok else None, error=msg if not ok else None)


class ListRssFeedsTool:
    name: str = "list_rss_feeds"
    description: str = (
        "Lists all RSS/Atom feed subscriptions. "
        "(RU: покажи подписки, список лент / UK: покажи підписки)"
    )
    args_schema = ListRssFeedsArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:list|show)\s+(?:my\s+)?(?:rss\s+)?(?:feed[s]?|subscriptions?)$",
            priority=452,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:покажи|список)\s+(?:моих?\s+)?(?:подписк[иу]|лент[уы])$",
            priority=452,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:покажи|список)\s+(?:моїх?\s+)?підписок$",
            priority=452,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: ListRssFeedsArgs) -> ToolResult:
        feeds = await asyncio.to_thread(_load_feeds)
        if not feeds:
            return ToolResult(
                success=True,
                output="No RSS subscriptions yet. Use subscribe_rss to add one.",
            )
        lines = [f"📰 RSS Subscriptions ({len(feeds)}):"]
        for i, f in enumerate(feeds, 1):
            lines.append(f"  {i}. {f.get('name', '?')} — {f.get('url', '?')}")
        return ToolResult(success=True, output="\n".join(lines))


class GetLatestNewsTool:
    name: str = "get_latest_news"
    description: str = (
        "Fetches and displays the latest news items from RSS/Atom "
        "subscriptions. Can filter by feed name. "
        "(RU: что нового, покажи новости / UK: що нового, покажи новини)"
    )
    args_schema = GetLatestNewsArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:what's\s+new|show\s+news|latest\s+news)$",
            priority=452,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^show\s+(?:me\s+)?(?:the\s+)?news$",
            priority=452,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^что\s+нового$",
            priority=452,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^покажи\s+новости$",
            priority=452,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^що\s+нового$",
            priority=452,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^покажи\s+новини$",
            priority=452,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: GetLatestNewsArgs) -> ToolResult:
        feeds = await asyncio.to_thread(_load_feeds)
        if not feeds:
            return ToolResult(
                success=True,
                output="No RSS subscriptions. Add one first with subscribe_rss.",
            )

        # Filter by name if specified
        if args.feed_name:
            q = args.feed_name.lower()
            feeds = [f for f in feeds if q in f.get("name", "").lower()]
            if not feeds:
                return ToolResult(
                    success=False,
                    error=f"No feed matching \"{args.feed_name}\".",
                )

        all_items: list[str] = []
        errors: list[str] = []

        for feed in feeds:
            url = feed.get("url", "")
            name = feed.get("name", url)
            xml_text, err = await asyncio.to_thread(_fetch_feed, url)
            if err:
                errors.append(f"  ⚠ {name}: {err}")
                continue
            items = _parse_feed_xml(xml_text or "")[:args.count]
            if items:
                all_items.append(f"\n📰 {name}:")
                for item in items:
                    title = item.get("title", "No title")
                    link = item.get("link", "")
                    all_items.append(f"  • {title}")
                    if link:
                        all_items.append(f"    {link}")

        if not all_items and not errors:
            return ToolResult(success=True, output="No news items found.")

        output_parts = []
        if all_items:
            # Security Layer 3: feed titles/links are untrusted external
            # content — strip prompt-injection payloads before they reach
            # the LLM context.
            from jarvis.security.output_sanitizer import clean_external_text

            cleaned = clean_external_text("\n".join(all_items), source="rss_reader")
            output_parts.append(cleaned)
        if errors:
            output_parts.append("Errors:\n" + "\n".join(errors))

        return ToolResult(success=True, output="\n".join(output_parts))
