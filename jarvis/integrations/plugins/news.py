"""News integration — top headlines, search by topic.

Off by default. Uses NewsAPI.org free tier. No extra packages (httpx).
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_NEWSAPI = "https://newsapi.org/v2"


class NewsIntegration(BaseIntegration):
    name = "news"
    display_name = "News"
    category = "information"
    description = "Top news headlines and search by topic via NewsAPI."
    icon = "\U0001f4f0"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "NewsAPI.org API Key", "type": "password"},
    ]
    required_packages = []

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
            _fn("news_top", "Top headlines.",
                {"country": {"type": "string", "description": "e.g. us, ua"}},
                ),
            _fn("news_search", "Search news by topic.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("news_tech", "Top technology news.", {}),
            _fn("news_ukraine", "Top news from Ukraine.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^latest\s+news$", "news_top", 300),
            (r"^\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435\s+\u043d\u043e\u0432\u043e\u0441\u0442\u0438$", "news_top", 300),
            (r"^\u043e\u0441\u0442\u0430\u043d\u043d\u0456\s+\u043d\u043e\u0432\u0438\u043d\u0438$", "news_top", 300),
            (r"^tech\s+news$", "news_tech", 310),
            (r"^\u043d\u043e\u0432\u043e\u0441\u0442\u0438\s+\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0439$", "news_tech", 310),
            (r"^news\s+about\s+ukraine$", "news_ukraine", 310),
            (r"^\u043d\u043e\u0432\u043e\u0441\u0442\u0438\s+\u0443\u043a\u0440\u0430\u0438\u043d\u044b$", "news_ukraine", 310),
            (r"^\u043d\u043e\u0432\u0438\u043d\u0438\s+\u0443\u043a\u0440\u0430\u0457\u043d\u0438$", "news_ukraine", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        key = self._api_key()
        if key is None:
            return "News is not connected, sir."
        try:
            import httpx  # noqa: E402
            async with httpx.AsyncClient(timeout=10) as client:
                if tool_name == "news_top":
                    return await self._top(client, key, args)
                if tool_name == "news_search":
                    return await self._search(client, key, args)
                if tool_name == "news_tech":
                    return await self._top(client, key, {"country": "us", "category": "technology"})
                if tool_name == "news_ukraine":
                    return await self._top(client, key, {"country": "ua"})
        except Exception as e:
            log.warning("[news] %s failed", tool_name, exc_info=True)
            return f"News error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _top(self, client, key: str, args: dict) -> str:
        params = {"apiKey": key, "pageSize": 5}
        params["country"] = args.get("country", "us")
        if "category" in args:
            params["category"] = args["category"]
        r = await client.get(f"{_NEWSAPI}/top-headlines", params=params)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        if not articles:
            return "No news found, sir."
        lines = [f"  \u2022 {a['title']}" for a in articles[:5]]
        return "Top news, sir:\n" + "\n".join(lines)

    async def _search(self, client, key: str, args: dict) -> str:
        query = args.get("query", "")
        r = await client.get(
            f"{_NEWSAPI}/everything",
            params={"apiKey": key, "q": query, "pageSize": 5, "sortBy": "publishedAt"},
        )
        r.raise_for_status()
        articles = r.json().get("articles", [])
        if not articles:
            return f"No news about '{query}', sir."
        lines = [f"  \u2022 {a['title']}" for a in articles[:5]]
        return f"News about '{query}', sir:\n" + "\n".join(lines)


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
