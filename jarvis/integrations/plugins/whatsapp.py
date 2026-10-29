"""WhatsApp integration — send/read/search via Playwright (WhatsApp Web).

Off by default. No external dependencies beyond Playwright which is
already available in the project.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class WhatsAppIntegration(BaseIntegration):
    name = "whatsapp"
    display_name = "WhatsApp"
    category = "communication"
    description = "Send and read WhatsApp messages via WhatsApp Web."
    icon = "\U0001f4ac"

    auth_type = "none"
    auth_fields = []
    required_packages = []  # Playwright already available

    _browser = None
    _page = None

    async def connect(self) -> bool:
        try:
            from playwright.async_api import async_playwright  # noqa: E402
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch_persistent_context(
                user_data_dir="whatsapp_session", headless=False,
            )
            self._page = (
                self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            )
            await self._page.goto("https://web.whatsapp.com")
            self.connected = True
        except Exception:
            log.warning("[whatsapp] connect failed", exc_info=True)
            self.connected = False
        return self.connected

    async def disconnect(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._page = None
        self.connected = False

    async def health_check(self) -> bool:
        return self._page is not None and self.connected

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("whatsapp_send", "Send a WhatsApp message.",
                {"contact": {"type": "string"}, "message": {"type": "string"}},
                required=["contact", "message"]),
            _fn("whatsapp_read", "Read recent messages from a chat.",
                {"contact": {"type": "string"}}, required=["contact"]),
            _fn("whatsapp_search", "Search for a chat.",
                {"query": {"type": "string"}}, required=["query"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^send\s+whatsapp\s+to\s+(.+)$", "whatsapp_send", 320),
            (
                r"^\u043e\u0442\u043f\u0440\u0430\u0432\u044c\s+\u0432\s+\u0432\u043e\u0442\u0441\u0430\u043f\s+(.+)$",
                "whatsapp_send",
                320,
            ),
            (
                r"^\u0432\u0456\u0434\u043f\u0440\u0430\u0432\s+\u0432\s+\u0432\u043e\u0442\u0441\u0430\u043f\s+(.+)$",
                "whatsapp_send",
                320,
            ),
            (r"^check\s+whatsapp$", "whatsapp_read", 300),
            (
                r"^\u043f\u0440\u043e\u0432\u0435\u0440\u044c\s+\u0432\u043e\u0442\u0441\u0430\u043f$",
                "whatsapp_read",
                300,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0456\u0440\s+\u0432\u043e\u0442\u0441\u0430\u043f$",
                "whatsapp_read",
                300,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if self._page is None:
            return "WhatsApp is not connected, sir."
        try:
            if tool_name == "whatsapp_send":
                return await self._send(args)
            if tool_name == "whatsapp_read":
                return await self._read(args)
            if tool_name == "whatsapp_search":
                return await self._search(args)
        except Exception as e:
            log.warning("[whatsapp] %s failed", tool_name, exc_info=True)
            return f"WhatsApp error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _send(self, args: dict) -> str:
        contact = args.get("contact", "")
        message = args.get("message", "")
        search_box = await self._page.wait_for_selector(
            'div[contenteditable="true"][data-tab="3"]', timeout=10000,
        )
        await search_box.fill(contact)
        await self._page.keyboard.press("Enter")
        await self._page.wait_for_timeout(2000)
        msg_box = await self._page.wait_for_selector(
            'div[contenteditable="true"][data-tab="10"]', timeout=10000,
        )
        await msg_box.fill(message)
        await self._page.keyboard.press("Enter")
        return f"Sent to {contact}, sir."

    async def _read(self, args: dict) -> str:
        contact = args.get("contact", "")
        search_box = await self._page.wait_for_selector(
            'div[contenteditable="true"][data-tab="3"]', timeout=10000,
        )
        await search_box.fill(contact)
        await self._page.keyboard.press("Enter")
        await self._page.wait_for_timeout(2000)
        messages = await self._page.query_selector_all("div.message-in span.selectable-text")
        if not messages:
            return f"No messages from {contact}, sir."
        texts = []
        for m in messages[-5:]:
            text = await m.inner_text()
            texts.append(f"  \u2022 {text}")
        return f"Recent from {contact}, sir:\n" + "\n".join(texts)

    async def _search(self, args: dict) -> str:
        query = args.get("query", "")
        search_box = await self._page.wait_for_selector(
            'div[contenteditable="true"][data-tab="3"]', timeout=10000,
        )
        await search_box.fill(query)
        await self._page.wait_for_timeout(2000)
        chats = await self._page.query_selector_all("span[title]")
        if not chats:
            return f"No chats matching '{query}', sir."
        names = []
        for c in chats[:5]:
            name = await c.get_attribute("title")
            if name:
                names.append(f"  \u2022 {name}")
        return "Matching chats, sir:\n" + "\n".join(names)


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
