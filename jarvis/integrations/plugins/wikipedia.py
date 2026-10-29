"""Wikipedia integration — search, summary, random article.

Off by default.  Public API, no auth needed.  httpx only.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class WikipediaIntegration(BaseIntegration):
    name = "wikipedia"
    display_name = "Wikipedia"
    category = "information"
    description = "Search Wikipedia, read summaries, get random articles."
    icon = "\U0001f4da"

    auth_type = "none"
    auth_fields: list[dict] = []
    required_packages: list[str] = []

    _BASE = "https://en.wikipedia.org/api/rest_v1"
    _ACTION = "https://en.wikipedia.org/w/api.php"

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return True

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("wiki_search", "Search Wikipedia articles.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("wiki_summary", "Get a short summary of a Wikipedia article.",
                {"title": {"type": "string"}}, required=["title"]),
            _fn("wiki_random", "Get a random Wikipedia article.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^what\s+is\s+(.+)$", "wiki_summary", 200),
            (r"^что\s+такое\s+(.+)$", "wiki_summary", 200),
            (r"^що\s+таке\s+(.+)$", "wiki_summary", 200),
            (r"^wikipedia\s+(?:about\s+)?(.+)$", "wiki_search", 300),
            (r"^википедия\s+(?:про\s+)?(.+)$", "wiki_search", 300),
            (r"^вікіпедія\s+(?:про\s+)?(.+)$", "wiki_search", 300),
            (r"^random\s+(?:wikipedia\s+)?article$", "wiki_random", 300),
            (r"^случайная\s+статья$", "wiki_random", 300),
            (r"^випадкова\s+стаття$", "wiki_random", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        import httpx  # lazy

        try:
            if tool_name == "wiki_search":
                return await self._search(args.get("query", ""))
            if tool_name == "wiki_summary":
                return await self._summary(args.get("title", ""))
            if tool_name == "wiki_random":
                return await self._random()
        except Exception as e:
            log.warning("[wikipedia] %s failed", tool_name, exc_info=True)
            return f"Wikipedia error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _search(self, query: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(self._ACTION, params={
                "action": "query", "list": "search",
                "srsearch": query, "srlimit": 10, "format": "json",
            })
            r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return f"No results for '{query}', sir."
        lines = [f"  \u2022 {a.get('title', '?')}" for a in results[:10]]
        return "Wikipedia results, sir:\n" + "\n".join(lines)

    async def _summary(self, title: str) -> str:
        import httpx

        safe_title = title.replace(" ", "_")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/page/summary/{safe_title}",
                            follow_redirects=True)
            if r.status_code == 404:
                return f"No Wikipedia article for '{title}', sir."
            r.raise_for_status()
        data = r.json()
        extract = data.get("extract", "No summary available.")
        return f"{data.get('title', title)}\n\n{extract[:1500]}"

    async def _random(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/page/random/summary",
                            follow_redirects=True)
            r.raise_for_status()
        data = r.json()
        return f"{data.get('title', '?')}\n\n{data.get('extract', '')[:1000]}"


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
