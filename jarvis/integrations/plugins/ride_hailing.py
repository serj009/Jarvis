"""Ride-hailing integration — estimate, request, status (Uber/Bolt API).

Off by default.  Uses a generic REST approach over httpx.  The user
configures the provider's base_url and API key.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class RideHailingIntegration(BaseIntegration):
    name = "ride_hailing"
    display_name = "Ride Hailing"
    category = "transport"
    description = "Estimate cost, request rides, check ride status (Uber/Bolt)."
    icon = "\U0001f695"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Ride API Token", "type": "password"},
        {"name": "base_url", "label": "Provider API URL", "type": "text"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _base_url(self) -> str:
        if self.auth and hasattr(self.auth, "get_field"):
            u = self.auth.get_field(self.name, "base_url")
            if u:
                return u.rstrip("/")
        return "https://api.uber.com/v1.2"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[ride_hailing] no token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("ride_estimate", "Get a ride cost estimate.",
                {"start_lat": {"type": "number"}, "start_lng": {"type": "number"},
                 "end_lat": {"type": "number"}, "end_lng": {"type": "number"}},
                required=["start_lat", "start_lng", "end_lat", "end_lng"]),
            _fn("ride_request", "Request a ride.",
                {"start_lat": {"type": "number"}, "start_lng": {"type": "number"},
                 "end_lat": {"type": "number"}, "end_lng": {"type": "number"},
                 "product_id": {"type": "string"}},
                required=["start_lat", "start_lng", "end_lat", "end_lng"]),
            _fn("ride_status", "Check current ride status.",
                {"request_id": {"type": "string"}}, required=["request_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^how\s+much\s+is\s+(?:uber|bolt|taxi)\s+to\s+(.+)$",
             "ride_estimate", 300),
            (r"^сколько\s+стоит\s+такси\s+до\s+(.+)$", "ride_estimate", 300),
            (r"^скільки\s+коштує\s+таксі\s+до\s+(.+)$", "ride_estimate", 300),
            (r"^(?:call|order|request)\s+(?:an?\s+)?(?:uber|bolt|taxi)$",
             "ride_request", 320),
            (r"^закажи\s+такси$", "ride_request", 320),
            (r"^замов\s+таксі$", "ride_request", 320),
            (r"^ride\s+status$", "ride_status", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Ride hailing is not connected, sir."

        base = self._base_url()
        try:
            if tool_name == "ride_estimate":
                return await self._estimate(base, args)
            if tool_name == "ride_request":
                return await self._request(base, args)
            if tool_name == "ride_status":
                return await self._status(base, args.get("request_id", ""))
        except Exception as e:
            log.warning("[ride_hailing] %s failed", tool_name, exc_info=True)
            return f"Ride error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _estimate(self, base: str, args: dict) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/estimates/price", headers=self._headers(),
                            params={"start_latitude": args["start_lat"],
                                    "start_longitude": args["start_lng"],
                                    "end_latitude": args["end_lat"],
                                    "end_longitude": args["end_lng"]})
            r.raise_for_status()
        prices = r.json().get("prices", r.json() if isinstance(r.json(), list) else [])
        if not prices:
            return "No estimates available, sir."
        lines = [f"  \u2022 {p.get('display_name', '?')}: {p.get('estimate', '?')}"
                 for p in prices[:5]]
        return "Ride estimates, sir:\n" + "\n".join(lines)

    async def _request(self, base: str, args: dict) -> str:
        import httpx

        body = {"start_latitude": args["start_lat"],
                "start_longitude": args["start_lng"],
                "end_latitude": args["end_lat"],
                "end_longitude": args["end_lng"]}
        if args.get("product_id"):
            body["product_id"] = args["product_id"]
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/requests",
                             headers=self._headers(), json=body)
            r.raise_for_status()
        req_id = r.json().get("request_id", "?")
        return f"Ride requested (id: {req_id}), sir."

    async def _status(self, base: str, request_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/requests/{request_id}",
                            headers=self._headers())
            r.raise_for_status()
        data = r.json()
        status = data.get("status", "unknown")
        driver = data.get("driver", {}).get("name", "")
        eta = data.get("eta", "?")
        msg = f"Ride status: {status}"
        if driver:
            msg += f", driver: {driver}"
        if eta != "?":
            msg += f", ETA: {eta} min"
        return msg + ", sir."


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
