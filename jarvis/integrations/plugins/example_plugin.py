"""Example integration — a self-contained reference + test fixture.

Implements the full BaseIntegration contract with no network and no real
auth (auth_type="none", no required_packages), so it is always discoverable
and enable-able. Doubles as the template for real plugins.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class ExampleIntegration(BaseIntegration):
    name = "example"
    display_name = "Example (Test)"
    category = "other"
    description = "A no-op reference integration used by tests and as a template."
    icon = "\U0001f9ea"

    auth_type = "none"
    auth_fields: list[dict] = []
    required_packages: list[str] = []

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return self.connected

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "example_ping",
                    "description": "Return a friendly pong (test tool).",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if tool_name == "example_ping":
            return "pong, sir."
        return f"Unknown tool: {tool_name}"

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^example\s+ping$", "example_ping", 300),
            (r"^пример\s+пинг$", "example_ping", 300),
        ]

    def get_proactive_data(self) -> list[dict] | None:
        # Emits nothing by default; a real plugin returns events/birthdays.
        return None
