"""Trello integration — boards, cards, create, move.

Off by default.  Uses the Trello REST API with API Key + Token (api_key
auth).  Only httpx is needed.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class TrelloIntegration(BaseIntegration):
    name = "trello"
    display_name = "Trello"
    category = "productivity"
    description = "Manage Trello boards, lists, and cards."
    icon = "\U0001f4cb"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Trello API Key", "type": "password"},
        {"name": "api_token", "label": "Trello Token", "type": "password"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://api.trello.com/1"

    # -- auth helpers ----------------------------------------------------

    def _creds(self) -> tuple[str | None, str | None]:
        if self.auth is None:
            return None, None
        key = self.auth.get_api_key(self.name)
        token = getattr(self.auth, "get_field", lambda *a: None)(self.name, "api_token")
        if token is None and hasattr(self.auth, "_fields"):
            token = self.auth._fields.get("api_token")
        return key, token

    def _params(self) -> dict:
        key, token = self._creds()
        return {"key": key or "", "token": token or ""}

    async def connect(self) -> bool:
        key, token = self._creds()
        self.connected = bool(key and token)
        if not self.connected:
            log.info("[trello] no API key/token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        key, token = self._creds()
        return bool(key and token)

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("trello_boards", "List your Trello boards.", {}),
            _fn("trello_cards", "List cards on a board.",
                {"board_id": {"type": "string"}}, required=["board_id"]),
            _fn("trello_create_card", "Create a new card.",
                {"list_id": {"type": "string"}, "name": {"type": "string"},
                 "desc": {"type": "string"}},
                required=["list_id", "name"]),
            _fn("trello_move_card", "Move a card to another list.",
                {"card_id": {"type": "string"}, "list_id": {"type": "string"}},
                required=["card_id", "list_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^show\s+trello\s+boards$", "trello_boards", 300),
            (r"^trello\s+boards$", "trello_boards", 300),
            (r"^покажи\s+доски\s+trello$", "trello_boards", 300),
            (r"^покажи\s+дошки\s+trello$", "trello_boards", 300),
            (r"^create\s+(?:trello\s+)?card\s+(.+)$", "trello_create_card", 320),
            (r"^создай\s+карточку\s+(.+)$", "trello_create_card", 320),
            (r"^створи\s+картку\s+(.+)$", "trello_create_card", 320),
            (r"^move\s+(?:trello\s+)?card\s+(.+)$", "trello_move_card", 320),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        key, token = self._creds()
        if not key or not token:
            return "Trello is not connected, sir."
        import httpx  # lazy

        try:
            if tool_name == "trello_boards":
                return await self._boards()
            if tool_name == "trello_cards":
                return await self._cards(args.get("board_id", ""))
            if tool_name == "trello_create_card":
                return await self._create_card(args)
            if tool_name == "trello_move_card":
                return await self._move_card(args)
        except Exception as e:
            log.warning("[trello] %s failed", tool_name, exc_info=True)
            return f"Trello error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _boards(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/members/me/boards",
                            params=self._params())
            r.raise_for_status()
        boards = r.json()
        if not boards:
            return "No boards found, sir."
        lines = [f"  \u2022 {b.get('name', '?')} ({b.get('id', '?')})"
                 for b in boards[:15]]
        return "Your Trello boards, sir:\n" + "\n".join(lines)

    async def _cards(self, board_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/boards/{board_id}/cards",
                            params=self._params())
            r.raise_for_status()
        cards = r.json()
        if not cards:
            return "No cards on that board, sir."
        lines = [f"  \u2022 {cd.get('name', '?')} ({cd.get('id', '?')})"
                 for cd in cards[:20]]
        return f"{len(cards)} card(s), sir:\n" + "\n".join(lines)

    async def _create_card(self, args: dict) -> str:
        import httpx

        params = {**self._params(), "idList": args["list_id"],
                  "name": args["name"], "desc": args.get("desc", "")}
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/cards", params=params)
            r.raise_for_status()
        return f"Card '{args['name']}' created, sir."

    async def _move_card(self, args: dict) -> str:
        import httpx

        params = {**self._params(), "idList": args["list_id"]}
        async with httpx.AsyncClient() as c:
            r = await c.put(f"{self._BASE}/cards/{args['card_id']}",
                            params=params)
            r.raise_for_status()
        return "Card moved, sir."


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
