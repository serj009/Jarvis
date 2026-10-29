"""YouTube integration — search, trending, play, subscriptions.

Off by default. Uses YouTube Data API v3 key.
"""

from __future__ import annotations

import logging
import webbrowser

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class YouTubeIntegration(BaseIntegration):
    name = "youtube"
    display_name = "YouTube"
    category = "media"
    description = "Search videos, trending, open in browser, check subscriptions."
    icon = "\u25b6\ufe0f"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "YouTube Data API Key", "type": "password"},
    ]
    required_packages = ["google-api-python-client"]

    auth: object | None = None

    def _api_key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._api_key())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._api_key())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("youtube_search", "Search YouTube videos.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("youtube_trending", "Show trending videos.",
                {"region": {"type": "string", "description": "country code, e.g. UA"}}),
            _fn("youtube_play", "Open a YouTube video in the browser.",
                {"video_id": {"type": "string"}}, required=["video_id"]),
            _fn("youtube_subscriptions", "List recent subscription videos.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^find\s+video\s+(?:about\s+)?(.+)$", "youtube_search", 300),
            (
                r"^\u043d\u0430\u0439\u0434\u0438\s+\u0432\u0438\u0434\u0435\u043e\s+(?:\u043f\u0440\u043e\s+)?(.+)$",
                "youtube_search",
                300,
            ),
            (
                r"^\u0437\u043d\u0430\u0439\u0434\u0438\s+\u0432\u0456\u0434\u0435\u043e\s+(?:\u043f\u0440\u043e\s+)?(.+)$",
                "youtube_search",
                300,
            ),
            (r"^what(?:'s|\s+is)\s+trending$", "youtube_trending", 310),
            (
                r"^\u0447\u0442\u043e\s+\u0432\s+\u0442\u0440\u0435\u043d\u0434\u0430\u0445$",
                "youtube_trending",
                310,
            ),
            (
                r"^\u0449\u043e\s+\u0432\s+\u0442\u0440\u0435\u043d\u0434\u0430\u0445$",
                "youtube_trending",
                310,
            ),
            (r"^play\s+(.+)\s+on\s+youtube$", "youtube_play", 320),
            (
                r"^\u0432\u043a\u043b\u044e\u0447\u0438\s+(.+)\s+\u043d\u0430\s+\u044e\u0442\u0443\u0431\u0435$",
                "youtube_play",
                320,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        key = self._api_key()
        if key is None:
            return "YouTube is not connected, sir."
        try:
            if tool_name == "youtube_search":
                return self._search(key, args)
            if tool_name == "youtube_trending":
                return self._trending(key, args)
            if tool_name == "youtube_play":
                return self._play(args)
            if tool_name == "youtube_subscriptions":
                return self._subscriptions(key)
        except Exception as e:
            log.warning("[youtube] %s failed", tool_name, exc_info=True)
            return f"YouTube error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    def get_proactive_data(self) -> list[dict] | None:
        if not self.connected:
            return None
        return None  # subscription checking in proactive loop

    # -- internals -------------------------------------------------------

    def _build_service(self, key: str):
        from googleapiclient.discovery import build  # noqa: E402
        return build("youtube", "v3", developerKey=key)

    def _search(self, key: str, args: dict) -> str:
        yt = self._build_service(key)
        resp = yt.search().list(
            q=args["query"], part="snippet", maxResults=5, type="video",
        ).execute()
        items = resp.get("items", [])
        if not items:
            return f"No videos found for '{args['query']}', sir."
        lines = [f"  \u2022 {i['snippet']['title']} (https://youtu.be/{i['id']['videoId']})"
                 for i in items]
        return "YouTube results, sir:\n" + "\n".join(lines)

    def _trending(self, key: str, args: dict) -> str:
        yt = self._build_service(key)
        region = args.get("region", "US")
        resp = yt.videos().list(
            part="snippet", chart="mostPopular", regionCode=region, maxResults=5,
        ).execute()
        items = resp.get("items", [])
        if not items:
            return "No trending videos, sir."
        lines = [f"  \u2022 {i['snippet']['title']}" for i in items]
        return f"Trending in {region}, sir:\n" + "\n".join(lines)

    def _play(self, args: dict) -> str:
        vid = args.get("video_id", "")
        url = f"https://www.youtube.com/watch?v={vid}"
        webbrowser.open(url)
        return f"Opening {url}, sir."

    def _subscriptions(self, key: str) -> str:
        yt = self._build_service(key)
        resp = yt.subscriptions().list(
            part="snippet", mine=True, maxResults=10,
        ).execute()
        items = resp.get("items", [])
        if not items:
            return "No subscriptions found, sir."
        lines = [f"  \u2022 {i['snippet']['title']}" for i in items]
        return "Your subscriptions, sir:\n" + "\n".join(lines)


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
