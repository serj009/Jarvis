"""Philips Hue integration — on/off, color, brightness, scene.

Off by default.  Uses the local Hue Bridge REST API over httpx.
The user provides the bridge API key (discovered via link-button flow
or the debug page).
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class PhilipsHueIntegration(BaseIntegration):
    name = "philips_hue"
    display_name = "Philips Hue"
    category = "smart_home"
    description = "Control Philips Hue lights: on/off, color, brightness, scenes."
    icon = "\U0001f4a1"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Hue Bridge API Key (username)", "type": "password"},
        {"name": "bridge_ip", "label": "Bridge IP (e.g. 192.168.1.100)", "type": "text"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _api_key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _bridge(self) -> str:
        if self.auth and hasattr(self.auth, "get_field"):
            ip = self.auth.get_field(self.name, "bridge_ip")
            if ip:
                return ip
        return "192.168.1.100"

    def _base(self) -> str:
        return f"http://{self._bridge()}/api/{self._api_key()}"

    async def connect(self) -> bool:
        self.connected = bool(self._api_key())
        if not self.connected:
            log.info("[hue] no API key yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._api_key())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("hue_on", "Turn on Hue light(s).",
                {"light_id": {"type": "string"}}, required=["light_id"]),
            _fn("hue_off", "Turn off Hue light(s).",
                {"light_id": {"type": "string"}}, required=["light_id"]),
            _fn("hue_color", "Change Hue light color.",
                {"light_id": {"type": "string"},
                 "color": {"type": "string", "description": "Color name or hex"}},
                required=["light_id", "color"]),
            _fn("hue_brightness", "Set Hue light brightness.",
                {"light_id": {"type": "string"},
                 "brightness": {"type": "integer", "description": "0-254"}},
                required=["light_id", "brightness"]),
            _fn("hue_scene", "Activate a Hue scene.",
                {"scene_id": {"type": "string"}}, required=["scene_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^lights?\s+blue$", "hue_color", 300),
            (r"^lights?\s+red$", "hue_color", 300),
            (r"^свет\s+синий$", "hue_color", 300),
            (r"^світло\s+синє$", "hue_color", 300),
            (r"^dim\s+(?:the\s+)?lights?$", "hue_brightness", 300),
            (r"^приглуши\s+свет$", "hue_brightness", 300),
            (r"^приглуши\s+світло$", "hue_brightness", 300),
            (r"^hue\s+on$", "hue_on", 300),
            (r"^hue\s+off$", "hue_off", 300),
            (r"^hue\s+scene\s+(.+)$", "hue_scene", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._api_key():
            return "Philips Hue is not connected, sir."

        try:
            if tool_name == "hue_on":
                return await self._set_state(args["light_id"], {"on": True})
            if tool_name == "hue_off":
                return await self._set_state(args["light_id"], {"on": False})
            if tool_name == "hue_color":
                return await self._set_color(args)
            if tool_name == "hue_brightness":
                return await self._set_state(args["light_id"],
                                             {"on": True, "bri": int(args["brightness"])})
            if tool_name == "hue_scene":
                return await self._activate_scene(args["scene_id"])
        except Exception as e:
            log.warning("[hue] %s failed", tool_name, exc_info=True)
            return f"Hue error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _set_state(self, light_id: str, state: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.put(f"{self._base()}/lights/{light_id}/state",
                            json=state)
            r.raise_for_status()
        action = "on" if state.get("on") else "off"
        if "bri" in state:
            action = f"brightness {state['bri']}"
        return f"Light {light_id} set to {action}, sir."

    async def _set_color(self, args: dict) -> str:
        color_map = {
            "red": {"hue": 0, "sat": 254},
            "blue": {"hue": 46920, "sat": 254},
            "green": {"hue": 25500, "sat": 254},
            "white": {"hue": 0, "sat": 0},
            "yellow": {"hue": 12750, "sat": 254},
            "purple": {"hue": 56100, "sat": 254},
        }
        color = args.get("color", "").lower()
        state = {"on": True}
        if color in color_map:
            state.update(color_map[color])
        return await self._set_state(args["light_id"], state)

    async def _activate_scene(self, scene_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.put(f"{self._base()}/groups/0/action",
                            json={"scene": scene_id})
            r.raise_for_status()
        return f"Scene {scene_id} activated, sir."


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
