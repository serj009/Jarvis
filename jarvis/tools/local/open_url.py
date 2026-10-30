"""Open a URL in the user's default browser.

The spoken response (ToolResult.output) is deliberately *not* the URL.
URLs read poorly through TTS ("h-t-t-p-s colon slash slash w-w-w dot
google dot com slash search question mark q equals…") and the LLM was
also generating markdown link syntax that bled into playback. The
output for a Google search URL is "Searching the web for <query>, sir.";
for any other URL it's "Opening <FriendlyName>, sir." where the friendly
name comes from a small site-name map (with a capitalised-first-label
fallback for unknown sites)."""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar
from urllib.parse import parse_qs, quote_plus, urlparse

from pydantic import BaseModel, Field

from jarvis.core.phrases import opening_phrase, say
from jarvis.platform import windows as winplat
from jarvis.tools.local.youtube import youtube_watch_url_from_url
from jarvis.tools.registry import ToolResult, VoicePattern

# Hand-curated readable names for sites that come up in conversational
# voice use. Extend as needed; unknown domains fall back to the
# capitalised first label.
_FRIENDLY_NAMES: dict[str, str] = {
    "google.com": "Google",
    "google.co.uk": "Google",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "github.com": "GitHub",
    "stackoverflow.com": "Stack Overflow",
    "wikipedia.org": "Wikipedia",
    "reddit.com": "Reddit",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
    "amazon.com": "Amazon",
    "netflix.com": "Netflix",
    "spotify.com": "Spotify",
    "discord.com": "Discord",
    "steamcommunity.com": "Steam",
    "steampowered.com": "Steam",
}

# Task 8a: distinguish a spoken DOMAIN ("uakino.best", "example.com") from a
# free-text search query ("лучшие фильмы 2024"). A domain is a dotted,
# space-free token whose last label is a known/plausible TLD. We open a
# domain directly as https://<domain>; anything else becomes a web search.
_COMMON_TLDS: frozenset[str] = frozenset((
    # generic
    "com", "org", "net", "info", "biz", "io", "co", "app", "dev", "ai",
    "best", "xyz", "online", "site", "shop", "store", "tv", "me", "cc",
    "gg", "fm", "to", "ly", "club", "tech", "news", "games", "stream",
    # country
    "ru", "ua", "pl", "by", "kz", "de", "fr", "uk", "us", "eu", "cn",
    "jp", "ca", "au", "in", "it", "es", "nl", "se", "no", "cz", "tr",
))

# A bare domain token: one or more dot-separated labels, last one a TLD.
# No scheme, no spaces, no path (the voice patterns capture a single token).
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+([a-z]{2,})$", re.IGNORECASE
)


def _looks_like_domain(text: str) -> bool:
    """True if *text* is a bare domain whose last label is a known TLD.

    Heuristic, deliberately conservative: a dotted space-free token like
    "uakino.best" or "example.co.uk" qualifies; "лучшие фильмы" or a token
    with a non-latin / unknown final label does not (it becomes a search)."""
    token = text.strip().rstrip(".")
    m = _DOMAIN_RE.match(token)
    if m is None:
        return False
    return m.group(1).lower() in _COMMON_TLDS


def _site_arg(spoken: str) -> dict:
    """Voice-pattern arg builder for 'open site <X>': open the domain
    directly when X is a domain, otherwise fall back to a web search."""
    token = spoken.strip()
    if _looks_like_domain(token):
        return {"url": "https://" + token.rstrip("/")}
    return {"url": "https://www.google.com/search?q=" + quote_plus(token)}


def _registered_domain(netloc: str) -> str:
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _friendly_name(netloc: str) -> str:
    domain = _registered_domain(netloc)
    if domain in _FRIENDLY_NAMES:
        return _FRIENDLY_NAMES[domain]
    label = domain.split(".")[0]
    return label.title() if label else domain


def _spoken_response_for(url: str) -> str:
    """Compose the TTS line for a successfully opened URL. Hides the URL
    entirely; spoken in the language of the request (jarvis.core.phrases)."""
    parsed = urlparse(url)
    domain = _registered_domain(parsed.netloc)
    if domain.startswith("google.") and parsed.path == "/search":
        q = parse_qs(parsed.query).get("q", [""])[0]
        if q:
            return say("searching_web", query=q)
    return opening_phrase(_friendly_name(parsed.netloc))


class OpenUrlArgs(BaseModel):
    url: str = Field(description="Absolute http(s) URL to open.")


class OpenUrlTool:
    name: str = "open_url"
    # Language-neutral on purpose: the user may speak Russian, Ukrainian or
    # English, so the model must decide from MEANING, not from English
    # trigger words. Websites and web searches go here; installed desktop
    # programs go to open_app.
    description: str = (
        "Opens a website in the default browser, or runs a web search. "
        "Use it when the user asks, in ANY language, to open a website, a "
        "web page or an online service (YouTube, Google, Wikipedia, GitHub, "
        "any domain like example.com), or to search / google something on "
        "the internet. Build a full https URL yourself, e.g. "
        "'https://www.youtube.com'. For a web search use "
        "'https://www.google.com/search?q=<query>'. Do NOT use for installed "
        "desktop programs (use open_app) or for playing music (use "
        "play_youtube_music, it opens the video directly). "
        "(RU: найди, загугли, поищи / UK: знайди, загугли, пошукай)"
    )
    args_schema = OpenUrlArgs
    requires_confirmation: bool = False
    # One-shot action: after a successful open the router ends the turn
    # instead of asking the model again (which re-opened the same tab).
    ends_turn_on_success: bool = True
    # 500: "search <q>" / "google <q>". The `(?:up|for)\s+` sits inside
    # the optional group WITH a trailing \s+ so a query starting with
    # "forty" is not shaved to "ty". quote_plus (not quote) — Google's
    # search URL wants "+" for spaces; a live test caught that.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:search|google)\s+(?:(?:up|for)\s+)?(.+)$",
            priority=500,
            args=lambda m: {
                "url": "https://www.google.com/search?q=" + quote_plus(m.group(1))
            },
        ),
        # RU: "найди ...", "загугли ...", "поищи ...", "погугли ..."
        VoicePattern(
            regex=r"^(?:найди|загугли|поищи|погугли)\s+(.+)$",
            priority=500,
            args=lambda m: {
                "url": "https://www.google.com/search?q=" + quote_plus(m.group(1))
            },
        ),
        # UK: "знайди ...", "загугли ...", "пошукай ...", "погугли ..."
        VoicePattern(
            regex=r"^(?:знайди|загугли|пошукай|погугли)\s+(.+)$",
            priority=500,
            args=lambda m: {
                "url": "https://www.google.com/search?q=" + quote_plus(m.group(1))
            },
        ),
        # RU/UK: "открой сайт X" / "відкрий сайт X". Task 8a: when X is a
        # bare domain (uakino.best) open it directly as https://X; otherwise
        # fall back to a web search. (Known targets like youtube are handled
        # by open_target_planner before voice patterns run.)
        VoicePattern(
            regex=r"^открой\s+сайт\s+(.+)$",
            priority=500,
            args=lambda m: _site_arg(m.group(1)),
        ),
        VoicePattern(
            regex=r"^відкрий\s+сайт\s+(.+)$",
            priority=500,
            args=lambda m: _site_arg(m.group(1)),
        ),
        # Task 8a: "открой/відкрий/open <domain>" WITHOUT the word "сайт",
        # e.g. "открой uakino.best". Only a bare domain token matches (the
        # file_manager extension patterns no longer steal these because a
        # TLD like .best is not a file extension). priority 450 < 500 so a
        # more specific pattern still wins, but these beat nothing generic.
        VoicePattern(
            regex=r"^(?:открой|открыть|відкрий|відкрити|open)\s+"
                  r"((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:/\S*)?)$",
            priority=450,
            args=lambda m: _site_arg(m.group(1)),
        ),
    )

    async def execute(self, args: OpenUrlArgs) -> ToolResult:
        parsed = urlparse(args.url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                success=False,
                error=f"only http(s) URLs are accepted, got scheme {parsed.scheme!r}",
            )
        if not parsed.netloc:
            return ToolResult(success=False, error="URL has no host")
        open_target = args.url
        spoken = _spoken_response_for(args.url)
        # LLM sometimes opens a YouTube search page instead of play_youtube_music.
        watch = await asyncio.to_thread(youtube_watch_url_from_url, args.url)
        if watch:
            open_target = watch
            q = parse_qs(urlparse(args.url).query).get("search_query", [""])[0]
            if q:
                spoken = say("playing", query=q)
            else:
                spoken = say("playing_unnamed")
        await asyncio.to_thread(winplat.open_url, open_target)
        return ToolResult(success=True, output=spoken)
