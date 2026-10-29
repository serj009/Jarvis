"""Google Drive integration — search, upload, download, list.

Off by default. Shares the Google OAuth token (see _google_common). Heavy
Google SDK imports are lazy (inside execute paths) so discovery works with
the packages absent.
"""

from __future__ import annotations

import logging
import os

from jarvis.integrations.base import BaseIntegration
from jarvis.integrations.plugins._google_common import GoogleIntegrationMixin

log = logging.getLogger(__name__)


class GoogleDriveIntegration(GoogleIntegrationMixin, BaseIntegration):
    name = "google_drive"
    display_name = "Google Drive"
    category = "productivity"
    description = "Search, list, upload, and download your Drive files."
    icon = "\U0001f4c1"
    scopes = ["https://www.googleapis.com/auth/drive"]

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("drive_list", "List recent Drive files.", {}),
            _fn(
                "drive_search",
                "Search Drive files by name.",
                {"query": {"type": "string"}},
                required=["query"],
            ),
            _fn(
                "drive_upload",
                "Upload a local file to Drive.",
                {"path": {"type": "string", "description": "local file path"}},
                required=["path"],
            ),
            _fn(
                "drive_download",
                "Download a Drive file by name to a local folder.",
                {
                    "query": {"type": "string"},
                    "dest": {"type": "string", "description": "local folder"},
                },
                required=["query"],
            ),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^list\s+my\s+drive\s+files$", "drive_list", 300),
            (r"^\u043f\u043e\u043a\u0430\u0436\u0438\s+\u0444\u0430\u0439\u043b\u044b\s+\u0434\u0438\u0441\u043a\u0430$", "drive_list", 300),
            (r"^\u043f\u043e\u043a\u0430\u0436\u0438\s+\u0444\u0430\u0439\u043b\u0438\s+\u0434\u0438\u0441\u043a\u0430$", "drive_list", 300),
            (r"^search\s+drive\s+(?:for\s+)?(.+)$", "drive_search", 310),
            (r"^\u043d\u0430\u0439\u0434\u0438\s+\u043d\u0430\s+\u0434\u0438\u0441\u043a\u0435\s+(.+)$", "drive_search", 310),
            (r"^\u0437\u043d\u0430\u0439\u0434\u0438\s+\u043d\u0430\s+\u0434\u0438\u0441\u043a\u0443\s+(.+)$", "drive_search", 310),
            (r"^upload\s+(?:to\s+drive\s+)?(.+)$", "drive_upload", 320),
            (r"^\u0437\u0430\u0433\u0440\u0443\u0437\u0438\s+\u043d\u0430\s+\u0434\u0438\u0441\u043a\s+(.+)$", "drive_upload", 320),
            (r"^\u0437\u0430\u0432\u0430\u043d\u0442\u0430\u0436\u0020?(?:\u043d\u0430\s+\u0434\u0438\u0441\u043a)\s+(.+)$", "drive_upload", 320),
            (r"^download\s+(?:from\s+drive\s+)?(.+)$", "drive_download", 320),
            (r"^\u0441\u043a\u0430\u0447\u0430\u0439\s+\u0441\s+\u0434\u0438\u0441\u043a\u0430\s+(.+)$", "drive_download", 320),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        try:
            svc = self._service("drive", "v3")
        except Exception as e:
            return str(e)
        try:
            if tool_name == "drive_list":
                return self._list(svc)
            if tool_name == "drive_search":
                return self._search(svc, args.get("query", ""))
            if tool_name == "drive_upload":
                return self._upload(svc, args.get("path", ""))
            if tool_name == "drive_download":
                return self._download(svc, args.get("query", ""), args.get("dest", "."))
        except Exception as e:
            log.warning("[google_drive] %s failed", tool_name, exc_info=True)
            return f"Drive error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _list(self, svc) -> str:
        resp = svc.files().list(
            pageSize=15, orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime)",
        ).execute()
        files = resp.get("files", [])
        if not files:
            return "No files in your Drive, sir."
        lines = [f"  \u2022 {f.get('name', '?')}" for f in files[:15]]
        return "Your Drive files, sir:\n" + "\n".join(lines)

    def _search(self, svc, query: str) -> str:
        safe = query.replace("'", "\\'")
        resp = svc.files().list(
            q=f"name contains '{safe}'", pageSize=15,
            fields="files(id,name,mimeType)",
        ).execute()
        files = resp.get("files", [])
        if not files:
            return f"No Drive files matching '{query}', sir."
        lines = [f"  \u2022 {f.get('name', '?')}" for f in files[:15]]
        return f"Found {len(files)} file(s), sir:\n" + "\n".join(lines)

    def _upload(self, svc, path: str) -> str:
        if not path or not os.path.isfile(path):
            return f"No such file: {path}, sir."
        from googleapiclient.http import MediaFileUpload  # lazy

        name = os.path.basename(path)
        media = MediaFileUpload(path, resumable=False)
        svc.files().create(
            body={"name": name}, media_body=media, fields="id",
        ).execute()
        return f"Uploaded '{name}' to Drive, sir."

    def _download(self, svc, query: str, dest: str) -> str:
        import io  # lazy

        from googleapiclient.http import MediaIoBaseDownload  # lazy

        resp = svc.files().list(
            q=f"name contains '{query.replace(chr(39), '')}'",
            pageSize=1, fields="files(id,name)",
        ).execute()
        files = resp.get("files", [])
        if not files:
            return f"No Drive file matching '{query}', sir."
        f = files[0]
        request = svc.files().get_media(fileId=f["id"])
        os.makedirs(dest, exist_ok=True)
        out_path = os.path.join(dest, f["name"])
        with open(out_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return f"Downloaded '{f['name']}' to {dest}, sir."


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
