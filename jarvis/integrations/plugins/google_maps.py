"""Google Maps integration — route, traffic, nearby places + proactive.

Off by default.  Uses the Google Maps Platform REST API (Directions, Places)
with an API key.  httpx only.  get_proactive_data() detects commute delays.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class GoogleMapsIntegration(BaseIntegration):
    name = "google_maps"
    display_name = "Google Maps"
    category = "transport"
    description = "Routes, traffic info, and nearby places via Google Maps."
    icon = "\U0001f5fa\ufe0f"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Google Maps API Key", "type": "password"},
        {"name": "home_address", "label": "Home address (for commute)", "type": "text"},
        {"name": "work_address", "label": "Work address (for commute)", "type": "text"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://maps.googleapis.com/maps/api"

    # -- auth helpers ----------------------------------------------------

    def _key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _field(self, name: str) -> str | None:
        if self.auth and hasattr(self.auth, "get_field"):
            return self.auth.get_field(self.name, name)
        return None

    async def connect(self) -> bool:
        self.connected = bool(self._key())
        if not self.connected:
            log.info("[google_maps] no API key yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._key())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("maps_route", "Get a route from A to B (time, distance).",
                {"origin": {"type": "string"}, "destination": {"type": "string"},
                 "mode": {"type": "string", "description": "driving|walking|transit"}},
                required=["origin", "destination"]),
            _fn("maps_traffic", "Check current traffic on a route.",
                {"origin": {"type": "string"}, "destination": {"type": "string"}},
                required=["origin", "destination"]),
            _fn("maps_nearby", "Find nearby places.",
                {"location": {"type": "string"},
                 "type": {"type": "string", "description": "e.g. restaurant, gas_station"},
                 "radius": {"type": "integer", "description": "Radius in meters"}},
                required=["location", "type"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^how\s+to\s+get\s+to\s+(.+)$", "maps_route", 300),
            (r"^как\s+добраться\s+до\s+(.+)$", "maps_route", 300),
            (r"^як\s+дістатися\s+до\s+(.+)$", "maps_route", 300),
            (r"^traffic\s+to\s+(.+)$", "maps_traffic", 300),
            (r"^пробки\s+до\s+(.+)$", "maps_traffic", 300),
            (r"^затори\s+до\s+(.+)$", "maps_traffic", 300),
            (r"^nearby\s+(.+)$", "maps_nearby", 250),
            (r"^ближайши[ей]\s+(.+)$", "maps_nearby", 250),
            (r"^найближч[іа]\s+(.+)$", "maps_nearby", 250),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._key():
            return "Google Maps is not connected, sir."
        import httpx  # lazy

        try:
            if tool_name == "maps_route":
                return await self._route(args)
            if tool_name == "maps_traffic":
                return await self._traffic(args)
            if tool_name == "maps_nearby":
                return await self._nearby(args)
        except Exception as e:
            log.warning("[google_maps] %s failed", tool_name, exc_info=True)
            return f"Maps error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Commute delay detection → ProactiveEngine."""
        if not self.connected or not self._key():
            return None
        home = self._field("home_address")
        work = self._field("work_address")
        if not home or not work:
            return None
        try:
            import httpx

            r = httpx.get(f"{self._BASE}/directions/json", params={
                "origin": home, "destination": work,
                "departure_time": "now", "key": self._key(),
            })
            r.raise_for_status()
            routes = r.json().get("routes", [])
            if not routes:
                return None
            leg = routes[0].get("legs", [{}])[0]
            normal = leg.get("duration", {}).get("value", 0)
            traffic = leg.get("duration_in_traffic", {}).get("value", 0)
            if traffic > normal * 1.3:
                return [{"type": "commute_delay",
                         "normal_min": normal // 60,
                         "traffic_min": traffic // 60}]
        except Exception:
            log.warning("[google_maps] proactive fetch failed", exc_info=True)
        return None

    # -- internals -------------------------------------------------------

    async def _route(self, args: dict) -> str:
        import httpx

        mode = args.get("mode", "driving")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/directions/json", params={
                "origin": args["origin"], "destination": args["destination"],
                "mode": mode, "departure_time": "now", "key": self._key(),
            })
            r.raise_for_status()
        routes = r.json().get("routes", [])
        if not routes:
            return "No route found, sir."
        leg = routes[0].get("legs", [{}])[0]
        dist = leg.get("distance", {}).get("text", "?")
        dur = leg.get("duration", {}).get("text", "?")
        traffic_dur = leg.get("duration_in_traffic", {}).get("text")
        msg = f"Route ({mode}): {dist}, {dur}"
        if traffic_dur:
            msg += f" (in traffic: {traffic_dur})"
        return msg + ", sir."

    async def _traffic(self, args: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/directions/json", params={
                "origin": args["origin"], "destination": args["destination"],
                "departure_time": "now", "key": self._key(),
            })
            r.raise_for_status()
        routes = r.json().get("routes", [])
        if not routes:
            return "No route found, sir."
        leg = routes[0].get("legs", [{}])[0]
        normal = leg.get("duration", {}).get("text", "?")
        traffic = leg.get("duration_in_traffic", {}).get("text", normal)
        return f"Normal: {normal}, with traffic: {traffic}, sir."

    async def _nearby(self, args: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/place/nearbysearch/json", params={
                "location": args["location"],
                "type": args["type"],
                "radius": args.get("radius", 1500),
                "key": self._key(),
            })
            r.raise_for_status()
        places = r.json().get("results", [])
        if not places:
            return f"No {args['type']} nearby, sir."
        lines = [f"  \u2022 {p.get('name', '?')} — {p.get('vicinity', '?')}"
                 for p in places[:10]]
        return f"Nearby {args['type']}, sir:\n" + "\n".join(lines)


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
