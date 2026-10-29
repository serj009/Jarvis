"""Telegram integration — send, read, send_file via a bot token.

Off by default. Unlike the Google suite this uses a simple API key (the
BotFather bot token), not OAuth. The heavy ``python-telegram-bot`` import is
lazy (inside execute paths) so registry discovery works with the package
absent — metadata, tool schemas, and voice patterns resolve with no deps.

Credentials: the bot token is stored as this integration's ``api_key`` (see
IntegrationAuth.store_api_key / get_api_key). ``send`` takes an explicit
``chat_id``; ``read`` and proactive use ``getUpdates`` so no stored chat id
is needed.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class TelegramIntegration(BaseIntegration):
    name = "telegram"
    display_name = "Telegram"
    category = "communication"
    description = "Send and read Telegram messages via your bot."
    icon = "\u2708\ufe0f"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Bot token (from @BotFather)", "type": "password"},
    ]
    required_packages = ["python-telegram-bot"]

    # Injected by the app/registry when available; tests can set directly.
    auth: object | None = None

    # -- auth/lifecycle --------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        token = self._token()
        self.connected = bool(token)
        if not self.connected:
            log.info("[telegram] no bot token yet — add it in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn(
                "telegram_send",
                "Send a Telegram message to a chat.",
                {
                    "chat_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                required=["chat_id", "text"],
            ),
            _fn("telegram_read", "Read recent incoming messages.", {}),
            _fn(
                "telegram_send_file",
                "Send a local file as a Telegram document.",
                {
                    "chat_id": {"type": "string"},
                    "path": {"type": "string", "description": "local file path"},
                },
                required=["chat_id", "path"],
            ),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^send\s+telegram\s+(.+)$", "telegram_send", 320),
            (
                r"^\u043e\u0442\u043f\u0440\u0430\u0432\u044c\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c\s+(.+)$",
                "telegram_send",
                320,
            ),
            (
                r"^\u0432\u0456\u0434\u043f\u0440\u0430\u0432\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c\s+(.+)$",
                "telegram_send",
                320,
            ),
            (r"^read\s+telegram$", "telegram_read", 300),
            (
                r"^\u043f\u0440\u043e\u0432\u0435\u0440\u044c\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c$",
                "telegram_read",
                300,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0456\u0440\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c$",
                "telegram_read",
                300,
            ),
            (r"^send\s+telegram\s+file\s+(.+)$", "telegram_send_file", 330),
            (
                r"^\u043e\u0442\u043f\u0440\u0430\u0432\u044c\s+\u0444\u0430\u0439\u043b\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c\s+(.+)$",
                "telegram_send_file",
                330,
            ),
            (
                r"^\u0432\u0456\u0434\u043f\u0440\u0430\u0432\s+\u0444\u0430\u0439\u043b\s+\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c\s+(.+)$",
                "telegram_send_file",
                330,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        token = self._token()
        if token is None:
            return "Telegram is not connected, sir."
        try:
            bot = _make_bot(token)
        except Exception as e:
            return str(e)
        try:
            if tool_name == "telegram_send":
                return await self._send(bot, args)
            if tool_name == "telegram_read":
                return await self._read(bot)
            if tool_name == "telegram_send_file":
                return await self._send_file(bot, args)
        except Exception as e:
            log.warning("[telegram] %s failed", tool_name, exc_info=True)
            return f"Telegram error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Unread-count signal from pending updates."""
        if not self.connected:
            return None
        return None  # live polling happens via the proactive loop, not here

    # -- internals -------------------------------------------------------

    async def _send(self, bot, args: dict) -> str:
        chat_id = args.get("chat_id", "")
        text = args.get("text", "")
        await bot.send_message(chat_id=chat_id, text=text)
        return f"Sent to chat {chat_id}, sir."

    async def _read(self, bot) -> str:
        updates = await bot.get_updates(limit=10)
        if not updates:
            return "No new Telegram messages, sir."
        lines = []
        for u in updates[:10]:
            msg = getattr(u, "message", None)
            if msg is None:
                continue
            who = getattr(getattr(msg, "from_user", None), "first_name", "?")
            lines.append(f"  \u2022 {who}: {getattr(msg, 'text', '')}")
        if not lines:
            return "No readable messages, sir."
        return "Your Telegram, sir:\n" + "\n".join(lines)

    async def _send_file(self, bot, args: dict) -> str:
        import os  # lazy, trivial

        chat_id = args.get("chat_id", "")
        path = args.get("path", "")
        if not path or not os.path.isfile(path):
            return f"No such file: {path}, sir."
        with open(path, "rb") as fh:
            await bot.send_document(chat_id=chat_id, document=fh)
        return f"Sent '{os.path.basename(path)}' to chat {chat_id}, sir."


def _make_bot(token: str):
    """Build a python-telegram-bot Bot, lazily. Raises if SDK absent."""
    from telegram import Bot  # noqa: E402  (lazy — only when connected)

    return Bot(token=token)


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
