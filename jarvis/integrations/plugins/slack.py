"""Slack integration — send, read, channels, status.

Off by default.  Uses the Slack Web API over httpx.  Bot User OAuth Token
as api_key.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class SlackIntegration(BaseIntegration):
    name = "slack"
    display_name = "Slack"
    category = "communication"
    description = "Send messages, read channels, manage your Slack status."
    icon = "\U0001f4ac"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Bot User OAuth Token", "type": "password"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://slack.com/api"

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json; charset=utf-8"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[slack] no token yet — add it in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("slack_send", "Send a Slack message to a channel or DM.",
                {"channel": {"type": "string"}, "text": {"type": "string"}},
                required=["channel", "text"]),
            _fn("slack_read", "Read recent messages from a channel.",
                {"channel": {"type": "string"}, "limit": {"type": "integer"}},
                required=["channel"]),
            _fn("slack_channels", "List Slack channels.", {}),
            _fn("slack_status", "Set your Slack status.",
                {"text": {"type": "string"}, "emoji": {"type": "string"}},
                required=["text"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^send\s+slack\s+(?:message\s+)?(.+)$", "slack_send", 320),
            (r"^отправь\s+в\s+слак\s+(.+)$", "slack_send", 320),
            (r"^відправ\s+в\s+слак\s+(.+)$", "slack_send", 320),
            (r"^check\s+slack$", "slack_read", 300),
            (r"^read\s+slack\s+(.+)$", "slack_read", 310),
            (r"^проверь\s+слак$", "slack_read", 300),
            (r"^перевір\s+слак$", "slack_read", 300),
            (r"^slack\s+channels$", "slack_channels", 300),
            (r"^каналы\s+слак$", "slack_channels", 300),
            (r"^slack\s+status\s+(.+)$", "slack_status", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Slack is not connected, sir."

        try:
            if tool_name == "slack_send":
                return await self._send(args)
            if tool_name == "slack_read":
                return await self._read(args)
            if tool_name == "slack_channels":
                return await self._channels()
            if tool_name == "slack_status":
                return await self._status(args)
        except Exception as e:
            log.warning("[slack] %s failed", tool_name, exc_info=True)
            return f"Slack error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _send(self, args: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/chat.postMessage",
                             headers=self._headers(),
                             json={"channel": args["channel"],
                                   "text": args["text"]})
            r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return f"Slack API error: {data.get('error', '?')}"
        return f"Sent to {args['channel']}, sir."

    async def _read(self, args: dict) -> str:
        import httpx

        limit = args.get("limit", 10)
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/conversations.history",
                            headers=self._headers(),
                            params={"channel": args["channel"],
                                    "limit": min(limit, 20)})
            r.raise_for_status()
        data = r.json()
        msgs = data.get("messages", [])
        if not msgs:
            return "No messages in that channel, sir."
        lines = [f"  \u2022 {m.get('text', '')[:80]}" for m in msgs[:10]]
        return f"Last {len(lines)} message(s), sir:\n" + "\n".join(lines)

    async def _channels(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/conversations.list",
                            headers=self._headers(),
                            params={"types": "public_channel,private_channel",
                                    "limit": 50})
            r.raise_for_status()
        channels = r.json().get("channels", [])
        if not channels:
            return "No channels found, sir."
        lines = [f"  \u2022 #{ch.get('name', '?')} ({ch.get('id', '?')})"
                 for ch in channels[:20]]
        return f"{len(channels)} channel(s), sir:\n" + "\n".join(lines)

    async def _status(self, args: dict) -> str:
        import httpx

        profile = {"status_text": args.get("text", ""),
                    "status_emoji": args.get("emoji", "")}
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/users.profile.set",
                             headers=self._headers(),
                             json={"profile": profile})
            r.raise_for_status()
        return f"Status set to '{args.get('text', '')}', sir."


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
