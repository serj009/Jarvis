"""Dropbox integration — search, upload, download, list.

Off by default.  OAuth2 auth.  Uses the ``dropbox`` SDK when available;
metadata/tools/voice patterns resolve without it.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class DropboxIntegration(BaseIntegration):
    name = "dropbox"
    display_name = "Dropbox"
    category = "storage"
    description = "Search, upload, download, and list Dropbox files."
    icon = "\U0001f4e6"

    auth_type = "oauth2"
    auth_fields = [
        {"name": "access_token", "label": "Dropbox OAuth2 Access Token", "type": "password"},
    ]
    required_packages = ["dropbox"]

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[dropbox] no access token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("dropbox_search", "Search files on Dropbox.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("dropbox_upload", "Upload a local file to Dropbox.",
                {"local_path": {"type": "string"},
                 "dropbox_path": {"type": "string"}},
                required=["local_path", "dropbox_path"]),
            _fn("dropbox_download", "Download a file from Dropbox.",
                {"dropbox_path": {"type": "string"},
                 "local_path": {"type": "string"}},
                required=["dropbox_path", "local_path"]),
            _fn("dropbox_list", "List files in a Dropbox folder.",
                {"path": {"type": "string", "description": "Folder path (empty = root)"}},
                required=[]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^find\s+on\s+dropbox\s+(.+)$", "dropbox_search", 300),
            (r"^dropbox\s+search\s+(.+)$", "dropbox_search", 300),
            (r"^найди\s+на\s+дропбоксе\s+(.+)$", "dropbox_search", 300),
            (r"^знайди\s+на\s+дропбоксі\s+(.+)$", "dropbox_search", 300),
            (r"^upload\s+to\s+dropbox\s+(.+)$", "dropbox_upload", 320),
            (r"^загрузи\s+на\s+дропбокс\s+(.+)$", "dropbox_upload", 320),
            (r"^download\s+from\s+dropbox\s+(.+)$", "dropbox_download", 320),
            (r"^dropbox\s+list(?:\s+(.+))?$", "dropbox_list", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Dropbox is not connected, sir."
        try:
            dbx = _make_client(self._token())
        except Exception as e:
            return str(e)

        try:
            if tool_name == "dropbox_search":
                return self._search(dbx, args.get("query", ""))
            if tool_name == "dropbox_upload":
                return self._upload(dbx, args)
            if tool_name == "dropbox_download":
                return self._download(dbx, args)
            if tool_name == "dropbox_list":
                return self._list(dbx, args.get("path", ""))
        except Exception as e:
            log.warning("[dropbox] %s failed", tool_name, exc_info=True)
            return f"Dropbox error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _search(self, dbx, query: str) -> str:
        result = dbx.files_search_v2(query)
        matches = result.matches
        if not matches:
            return f"Nothing found for '{query}', sir."
        lines = [f"  \u2022 {m.metadata.get_metadata().name}"
                 for m in matches[:10]]
        return f"Found {len(matches)} file(s), sir:\n" + "\n".join(lines)

    def _upload(self, dbx, args: dict) -> str:
        import dropbox as _dbx  # lazy

        with open(args["local_path"], "rb") as f:
            dbx.files_upload(f.read(), args["dropbox_path"],
                             mode=_dbx.files.WriteMode.overwrite)
        return f"Uploaded to {args['dropbox_path']}, sir."

    def _download(self, dbx, args: dict) -> str:
        dbx.files_download_to_file(args["local_path"], args["dropbox_path"])
        return f"Downloaded to {args['local_path']}, sir."

    def _list(self, dbx, path: str) -> str:
        result = dbx.files_list_folder(path or "")
        entries = result.entries
        if not entries:
            return "Empty folder, sir."
        lines = [f"  \u2022 {e.name}" for e in entries[:20]]
        return f"{len(entries)} item(s), sir:\n" + "\n".join(lines)


def _make_client(token: str):
    """Build a Dropbox client lazily."""
    import dropbox as _dbx  # noqa: E402

    return _dbx.Dropbox(token)


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
