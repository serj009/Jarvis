"""OneDrive integration — search, upload, download, list via Microsoft Graph.

Off by default.  OAuth2 auth via the ``msal`` library.  Graph REST calls
use httpx.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class OneDriveIntegration(BaseIntegration):
    name = "onedrive"
    display_name = "OneDrive"
    category = "storage"
    description = "Search, upload, download, and list OneDrive files."
    icon = "\u2601\ufe0f"

    auth_type = "oauth2"
    auth_fields = [
        {"name": "access_token", "label": "Microsoft Graph Access Token", "type": "password"},
    ]
    required_packages = ["msal"]

    auth: object | None = None

    _GRAPH = "https://graph.microsoft.com/v1.0"

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[onedrive] no access token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("onedrive_search", "Search files on OneDrive.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("onedrive_upload", "Upload a local file to OneDrive.",
                {"local_path": {"type": "string"},
                 "remote_path": {"type": "string"}},
                required=["local_path", "remote_path"]),
            _fn("onedrive_download", "Download a file from OneDrive.",
                {"item_id": {"type": "string"},
                 "local_path": {"type": "string"}},
                required=["item_id", "local_path"]),
            _fn("onedrive_list", "List files in a OneDrive folder.",
                {"folder_path": {"type": "string",
                                 "description": "Folder path (empty = root)"}},
                required=[]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^find\s+on\s+onedrive\s+(.+)$", "onedrive_search", 300),
            (r"^onedrive\s+search\s+(.+)$", "onedrive_search", 300),
            (r"^найди\s+на\s+onedrive\s+(.+)$", "onedrive_search", 300),
            (r"^знайди\s+на\s+onedrive\s+(.+)$", "onedrive_search", 300),
            (r"^upload\s+to\s+onedrive\s+(.+)$", "onedrive_upload", 320),
            (r"^загрузи\s+на\s+onedrive\s+(.+)$", "onedrive_upload", 320),
            (r"^download\s+from\s+onedrive\s+(.+)$", "onedrive_download", 320),
            (r"^onedrive\s+list(?:\s+(.+))?$", "onedrive_list", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "OneDrive is not connected, sir."
        import httpx  # lazy

        try:
            if tool_name == "onedrive_search":
                return await self._search(args.get("query", ""))
            if tool_name == "onedrive_upload":
                return await self._upload(args)
            if tool_name == "onedrive_download":
                return await self._download(args)
            if tool_name == "onedrive_list":
                return await self._list(args.get("folder_path", ""))
        except Exception as e:
            log.warning("[onedrive] %s failed", tool_name, exc_info=True)
            return f"OneDrive error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _search(self, query: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{self._GRAPH}/me/drive/root/search(q='{query}')",
                headers=self._headers())
            r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return f"Nothing found for '{query}', sir."
        lines = [f"  \u2022 {i.get('name', '?')} ({i.get('id', '?')})"
                 for i in items[:10]]
        return f"Found {len(items)} item(s), sir:\n" + "\n".join(lines)

    async def _upload(self, args: dict) -> str:
        import httpx

        remote = args["remote_path"]
        with open(args["local_path"], "rb") as f:
            data = f.read()
        async with httpx.AsyncClient() as c:
            r = await c.put(
                f"{self._GRAPH}/me/drive/root:/{remote}:/content",
                headers={**self._headers(),
                         "Content-Type": "application/octet-stream"},
                content=data)
            r.raise_for_status()
        return f"Uploaded to OneDrive:/{remote}, sir."

    async def _download(self, args: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{self._GRAPH}/me/drive/items/{args['item_id']}/content",
                headers=self._headers(), follow_redirects=True)
            r.raise_for_status()
        with open(args["local_path"], "wb") as f:
            f.write(r.content)
        return f"Downloaded to {args['local_path']}, sir."

    async def _list(self, folder_path: str) -> str:
        import httpx

        url = (f"{self._GRAPH}/me/drive/root:/{folder_path}:/children"
               if folder_path
               else f"{self._GRAPH}/me/drive/root/children")
        async with httpx.AsyncClient() as c:
            r = await c.get(url, headers=self._headers())
            r.raise_for_status()
        items = r.json().get("value", [])
        if not items:
            return "Empty folder, sir."
        lines = [f"  \u2022 {i.get('name', '?')}" for i in items[:20]]
        return f"{len(items)} item(s), sir:\n" + "\n".join(lines)


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
