"""Notion integration — search, read, create pages, add to DB.

Off by default.  Uses the Notion REST API v1 with an Internal Integration
Token (api_key).  The only network dependency is *httpx* (already in the
project); no extra packages are required.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class NotionIntegration(BaseIntegration):
    name = "notion"
    display_name = "Notion"
    category = "productivity"
    description = "Search, read, and create Notion pages and DB records."
    icon = "\U0001f4d3"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Notion Internal Integration Token", "type": "password"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://api.notion.com/v1"
    _VERSION = "2022-06-28"

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Notion-Version": self._VERSION,
            "Content-Type": "application/json",
        }

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[notion] no token yet — add it in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("notion_search", "Search Notion pages and databases.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("notion_read", "Read a Notion page by ID.",
                {"page_id": {"type": "string"}}, required=["page_id"]),
            _fn("notion_create", "Create a new Notion page.",
                {"parent_id": {"type": "string"}, "title": {"type": "string"},
                 "content": {"type": "string", "description": "Markdown body"}},
                required=["parent_id", "title"]),
            _fn("notion_add_to_db", "Add a record to a Notion database.",
                {"database_id": {"type": "string"},
                 "properties": {"type": "string", "description": "JSON properties"}},
                required=["database_id", "properties"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^find\s+in\s+notion\s+(.+)$", "notion_search", 300),
            (r"^notion\s+search\s+(.+)$", "notion_search", 300),
            (r"^найди\s+в\s+notion\s+(.+)$", "notion_search", 300),
            (r"^знайди\s+в\s+notion\s+(.+)$", "notion_search", 300),
            (r"^create\s+notion\s+page\s+(.+)$", "notion_create", 320),
            (r"^создай\s+страницу\s+в\s+notion\s+(.+)$", "notion_create", 320),
            (r"^створи\s+сторінку\s+в\s+notion\s+(.+)$", "notion_create", 320),
            (r"^read\s+notion\s+(.+)$", "notion_read", 310),
            (r"^прочти\s+notion\s+(.+)$", "notion_read", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Notion is not connected, sir."

        try:
            if tool_name == "notion_search":
                return await self._search(args.get("query", ""))
            if tool_name == "notion_read":
                return await self._read(args.get("page_id", ""))
            if tool_name == "notion_create":
                return await self._create(args)
            if tool_name == "notion_add_to_db":
                return await self._add_to_db(args)
        except Exception as e:
            log.warning("[notion] %s failed", tool_name, exc_info=True)
            return f"Notion error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _search(self, query: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/search",
                             headers=self._headers(),
                             json={"query": query, "page_size": 10})
            r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"Nothing found for '{query}', sir."
        lines = []
        for item in results[:10]:
            title_parts = (item.get("properties", {}).get("title", {})
                           .get("title", []))
            title = title_parts[0].get("plain_text", "Untitled") if title_parts else "Untitled"
            lines.append(f"  \u2022 {title} ({item.get('id', '?')})")
        return f"Found {len(results)} result(s), sir:\n" + "\n".join(lines)

    async def _read(self, page_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/blocks/{page_id}/children",
                            headers=self._headers())
            r.raise_for_status()
        blocks = r.json().get("results", [])
        texts = []
        for b in blocks[:30]:
            rich = b.get(b.get("type", ""), {}).get("rich_text", [])
            for rt in rich:
                texts.append(rt.get("plain_text", ""))
        return "\n".join(texts) if texts else "Empty page, sir."

    async def _create(self, args: dict) -> str:
        import httpx

        body = {
            "parent": {"page_id": args["parent_id"]},
            "properties": {
                "title": {"title": [{"text": {"content": args["title"]}}]},
            },
            "children": [{
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text",
                              "text": {"content": args.get("content", "")}}]},
            }] if args.get("content") else [],
        }
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/pages",
                             headers=self._headers(), json=body)
            r.raise_for_status()
        return f"Page '{args['title']}' created, sir."

    async def _add_to_db(self, args: dict) -> str:
        import json as _json

        import httpx

        props = _json.loads(args.get("properties", "{}"))
        body = {"parent": {"database_id": args["database_id"]},
                "properties": props}
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/pages",
                             headers=self._headers(), json=body)
            r.raise_for_status()
        return "Record added to database, sir."


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
