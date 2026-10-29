"""Twitch integration — live streams, search, open + proactive.

Off by default.  OAuth2 auth (Twitch).  httpx for Helix API calls.
get_proactive_data() signals when a favourite streamer goes live.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class TwitchIntegration(BaseIntegration):
    name = "twitch"
    display_name = "Twitch"
    category = "media"
    description = "Check live streams, search, and open Twitch channels."
    icon = "\U0001f3ae"

    auth_type = "oauth2"
    auth_fields = [
        {"name": "api_key", "label": "Twitch Client ID", "type": "password"},
        {"name": "access_token", "label": "Twitch OAuth Token", "type": "password"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://api.twitch.tv/helix"

    # -- auth helpers ----------------------------------------------------

    def _client_id(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _access_token(self) -> str | None:
        if self.auth is None:
            return None
        if hasattr(self.auth, "get_field"):
            return self.auth.get_field(self.name, "access_token")
        return None

    def _headers(self) -> dict:
        return {"Client-ID": self._client_id() or "",
                "Authorization": f"Bearer {self._access_token() or ''}"}

    async def connect(self) -> bool:
        self.connected = bool(self._client_id() and self._access_token())
        if not self.connected:
            log.info("[twitch] credentials missing — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._client_id() and self._access_token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("twitch_live", "Show live streams from followed channels.", {}),
            _fn("twitch_search", "Search for live streams.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("twitch_open", "Open a Twitch stream in the browser.",
                {"channel": {"type": "string"}}, required=["channel"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^who'?s\s+streaming$", "twitch_live", 300),
            (r"^кто\s+стримит$", "twitch_live", 300),
            (r"^хто\s+стрімить$", "twitch_live", 300),
            (r"^twitch\s+search\s+(.+)$", "twitch_search", 310),
            (r"^open\s+twitch\s+(.+)$", "twitch_open", 320),
            (r"^открой\s+твич\s+(.+)$", "twitch_open", 320),
            (r"^відкрий\s+твіч\s+(.+)$", "twitch_open", 320),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._client_id() or not self._access_token():
            return "Twitch is not connected, sir."

        try:
            if tool_name == "twitch_live":
                return await self._live()
            if tool_name == "twitch_search":
                return await self._search(args.get("query", ""))
            if tool_name == "twitch_open":
                return self._open(args.get("channel", ""))
        except Exception as e:
            log.warning("[twitch] %s failed", tool_name, exc_info=True)
            return f"Twitch error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Favourite streamer went live → ProactiveEngine."""
        if not self.connected:
            return None
        try:
            import httpx

            r = httpx.get(f"{self._BASE}/streams/followed",
                          headers=self._headers(),
                          params={"first": 10})
            if r.status_code != 200:
                return None
            streams = r.json().get("data", [])
            if not streams:
                return None
            return [{"type": "streamer_live",
                     "streams": [{"name": s.get("user_name", "?"),
                                  "game": s.get("game_name", "?")}
                                 for s in streams[:5]]}]
        except Exception:
            log.warning("[twitch] proactive fetch failed", exc_info=True)
            return None

    # -- internals -------------------------------------------------------

    async def _live(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/streams/followed",
                            headers=self._headers(),
                            params={"first": 10})
            r.raise_for_status()
        streams = r.json().get("data", [])
        if not streams:
            return "Nobody is streaming right now, sir."
        lines = [f"  \u2022 {s.get('user_name', '?')} — {s.get('game_name', '?')}"
                 for s in streams[:10]]
        return f"{len(streams)} live stream(s), sir:\n" + "\n".join(lines)

    async def _search(self, query: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/search/channels",
                            headers=self._headers(),
                            params={"query": query, "first": 10,
                                    "live_only": "true"})
            r.raise_for_status()
        channels = r.json().get("data", [])
        if not channels:
            return f"No live channels for '{query}', sir."
        lines = [f"  \u2022 {ch.get('display_name', '?')} — {ch.get('game_name', '?')}"
                 for ch in channels[:10]]
        return f"Found {len(channels)} channel(s), sir:\n" + "\n".join(lines)

    def _open(self, channel: str) -> str:
        import webbrowser

        url = f"https://twitch.tv/{channel}"
        webbrowser.open(url)
        return f"Opening {url}, sir."


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
            },
        },
    }
