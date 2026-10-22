"""Home Assistant integration — devices, turn_on/off, set, status.

Off by default.  Uses HA REST API with Long-Lived Access Token.  httpx only.
The user must provide the HA URL (ha_url) and token.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class HomeAssistantIntegration(BaseIntegration):
    name = "home_assistant"
    display_name = "Home Assistant"
    category = "smart_home"
    description = "Control Home Assistant devices: lights, climate, locks, etc."
    icon = "\U0001f3e0"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "HA Long-Lived Access Token", "type": "password"},
        {"name": "ha_url", "label": "Home Assistant URL (e.g. http://homeassistant.local:8123)", "type": "text"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _url(self) -> str:
        if self.auth and hasattr(self.auth, "get_field"):
            u = self.auth.get_field(self.name, "ha_url")
            if u:
                return u.rstrip("/")
        return "http://homeassistant.local:8123"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[home_assistant] no token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("ha_devices", "List Home Assistant devices/entities.", {}),
            _fn("ha_turn_on", "Turn on a device.",
                {"entity_id": {"type": "string"}}, required=["entity_id"]),
            _fn("ha_turn_off", "Turn off a device.",
                {"entity_id": {"type": "string"}}, required=["entity_id"]),
            _fn("ha_set", "Set a device attribute (temperature, brightness, etc.).",
                {"entity_id": {"type": "string"},
                 "attribute": {"type": "string"},
                 "value": {"type": "string"}},
                required=["entity_id", "attribute", "value"]),
            _fn("ha_status", "Get device status.",
                {"entity_id": {"type": "string"}}, required=["entity_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^turn\s+on\s+(?:the\s+)?(.+)$", "ha_turn_on", 250),
            (r"^включи\s+(.+)$", "ha_turn_on", 250),
            (r"^увімкни\s+(.+)$", "ha_turn_on", 250),
            (r"^turn\s+off\s+(?:the\s+)?(.+)$", "ha_turn_off", 250),
            (r"^выключи\s+(.+)$", "ha_turn_off", 250),
            (r"^вимкни\s+(.+)$", "ha_turn_off", 250),
            (r"^set\s+temperature\s+(?:to\s+)?(.+)$", "ha_set", 300),
            (r"^поставь\s+температуру\s+(.+)$", "ha_set", 300),
            (r"^постав\s+температуру\s+(.+)$", "ha_set", 300),
            (r"^(?:is\s+the\s+)?door\s+locked$", "ha_status", 250),
            (r"^дверь\s+закрыта$", "ha_status", 250),
            (r"^двері\s+зачинені$", "ha_status", 250),
            (r"^ha\s+devices$", "ha_devices", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Home Assistant is not connected, sir."
        import httpx  # lazy

        base = self._url()
        try:
            if tool_name == "ha_devices":
                return await self._devices(base)
            if tool_name == "ha_turn_on":
                return await self._call_service(base, "turn_on", args["entity_id"])
            if tool_name == "ha_turn_off":
                return await self._call_service(base, "turn_off", args["entity_id"])
            if tool_name == "ha_set":
                return await self._set_attr(base, args)
            if tool_name == "ha_status":
                return await self._status(base, args["entity_id"])
        except Exception as e:
            log.warning("[home_assistant] %s failed", tool_name, exc_info=True)
            return f"HA error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _devices(self, base: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/api/states", headers=self._headers())
            r.raise_for_status()
        entities = r.json()
        if not entities:
            return "No devices found, sir."
        lines = [f"  \u2022 {e.get('entity_id', '?')} — {e.get('state', '?')}"
                 for e in entities[:20]]
        return f"{len(entities)} device(s), sir:\n" + "\n".join(lines)

    async def _call_service(self, base: str, action: str, entity_id: str) -> str:
        import httpx

        domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/api/services/{domain}/{action}",
                             headers=self._headers(),
                             json={"entity_id": entity_id})
            r.raise_for_status()
        return f"{entity_id} turned {action.replace('turn_', '')}, sir."

    async def _set_attr(self, base: str, args: dict) -> str:
        import httpx

        entity_id = args["entity_id"]
        domain = entity_id.split(".")[0] if "." in entity_id else "climate"
        service = "set_temperature" if args["attribute"] == "temperature" else "turn_on"
        payload = {"entity_id": entity_id, args["attribute"]: args["value"]}
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/api/services/{domain}/{service}",
                             headers=self._headers(), json=payload)
            r.raise_for_status()
        return f"{args['attribute']} set to {args['value']} for {entity_id}, sir."

    async def _status(self, base: str, entity_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/api/states/{entity_id}",
                            headers=self._headers())
            r.raise_for_status()
        data = r.json()
        state = data.get("state", "unknown")
        friendly = data.get("attributes", {}).get("friendly_name", entity_id)
        return f"{friendly}: {state}, sir."


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
