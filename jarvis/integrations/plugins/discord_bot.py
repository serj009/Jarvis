"""Discord integration — send/read messages, manage status.

Off by default. Uses discord.py Bot token.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class DiscordIntegration(BaseIntegration):
    name = "discord"
    display_name = "Discord"
    category = "communication"
    description = "Send and read Discord messages, manage bot status."
    icon = "\U0001f3ae"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Discord Bot Token", "type": "password"},
    ]
    required_packages = ["discord.py"]

    auth: object | None = None

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("discord_send", "Send a message to a Discord channel.",
                {"channel_id": {"type": "string"}, "message": {"type": "string"}},
                required=["channel_id", "message"]),
            _fn("discord_read", "Read recent messages from a channel.",
                {"channel_id": {"type": "string"}, "limit": {"type": "integer"}},
                required=["channel_id"]),
            _fn("discord_status", "Set bot status.",
                {"status": {"type": "string", "description": "online/idle/dnd/invisible"},
                 "activity": {"type": "string"}},
                required=["status"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^send\s+discord\s+message\s+(.+)$", "discord_send", 320),
            (
                r"^\u043e\u0442\u043f\u0440\u0430\u0432\u044c\s+\u0432\s+\u0434\u0438\u0441\u043a\u043e\u0440\u0434\s+(.+)$",
                "discord_send",
                320,
            ),
            (
                r"^\u0432\u0456\u0434\u043f\u0440\u0430\u0432\s+\u0432\s+\u0434\u0438\u0441\u043a\u043e\u0440\u0434\s+(.+)$",
                "discord_send",
                320,
            ),
            (r"^check\s+discord$", "discord_read", 300),
            (
                r"^\u043f\u0440\u043e\u0432\u0435\u0440\u044c\s+\u0434\u0438\u0441\u043a\u043e\u0440\u0434$",
                "discord_read",
                300,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0456\u0440\s+\u0434\u0438\u0441\u043a\u043e\u0440\u0434$",
                "discord_read",
                300,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        token = self._token()
        if token is None:
            return "Discord is not connected, sir."
        try:
            import httpx  # noqa: E402
            headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=10) as client:
                if tool_name == "discord_send":
                    return await self._send(client, headers, args)
                if tool_name == "discord_read":
                    return await self._read(client, headers, args)
                if tool_name == "discord_status":
                    return self._status_info(args)
        except Exception as e:
            log.warning("[discord] %s failed", tool_name, exc_info=True)
            return f"Discord error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _send(self, client, headers: dict, args: dict) -> str:
        channel_id = args.get("channel_id", "")
        message = args.get("message", "")
        r = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            json={"content": message},
        )
        r.raise_for_status()
        return f"Sent to channel {channel_id}, sir."

    async def _read(self, client, headers: dict, args: dict) -> str:
        channel_id = args.get("channel_id", "")
        limit = min(int(args.get("limit", 10)), 50)
        r = await client.get(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            params={"limit": limit},
        )
        r.raise_for_status()
        messages = r.json()
        if not messages:
            return f"No messages in channel {channel_id}, sir."
        lines = [
            f"  \u2022 {m.get('author', {}).get('username', '?')}: {m.get('content', '')[:100]}"
            for m in messages[:10]
        ]
        return "Recent messages, sir:\n" + "\n".join(lines)

    def _status_info(self, args: dict) -> str:
        status = args.get("status", "online")
        activity = args.get("activity", "")
        return (f"Status change to '{status}' with activity '{activity}' "
                f"requires a running bot instance, sir. "
                f"Set it in your bot's main loop.")


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
