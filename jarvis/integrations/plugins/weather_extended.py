"""Extended weather integration — weekly forecast, alerts, air quality.

Off by default. Uses OpenWeatherMap API (free tier). Does NOT duplicate the
existing ``jarvis/tools/local/weather.py`` (Open-Meteo, current conditions).
This plugin adds: 7-day forecast, weather alerts, and air quality index.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_OWM_API = "https://api.openweathermap.org"


class WeatherExtendedIntegration(BaseIntegration):
    name = "weather_extended"
    display_name = "Weather Extended"
    category = "information"
    description = "Weekly forecast, weather alerts, and air quality (OpenWeatherMap)."
    icon = "\u26c5"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "OpenWeatherMap API Key", "type": "password"},
    ]
    required_packages = []

    auth: object | None = None

    def _api_key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._api_key())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._api_key())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("weather_week", "7-day weather forecast.",
                {"city": {"type": "string"}}, required=["city"]),
            _fn("weather_alerts", "Active weather alerts.",
                {"lat": {"type": "number"}, "lon": {"type": "number"}},
                required=["lat", "lon"]),
            _fn("weather_aqi", "Air quality index.",
                {"city": {"type": "string"}}, required=["city"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^weather\s+this\s+week$", "weather_week", 310),
            (
                r"^\u043f\u043e\u0433\u043e\u0434\u0430\s+\u043d\u0430\s+\u043d\u0435\u0434\u0435\u043b\u044e$",
                "weather_week",
                310,
            ),
            (
                r"^\u043f\u043e\u0433\u043e\u0434\u0430\s+\u043d\u0430\s+\u0442\u0438\u0436\u0434\u0435\u043d\u044c$",
                "weather_week",
                310,
            ),
            (r"^weather\s+alerts?$", "weather_alerts", 310),
            (
                r"^\u043f\u043e\u0433\u043e\u0434\u043d\u0456\s+\u043f\u043e\u043f\u0435\u0440\u0435\u0434\u0436\u0435\u043d\u043d\u044f$",
                "weather_alerts",
                310,
            ),
            (r"^air\s+quality$", "weather_aqi", 310),
            (
                r"^\u043a\u0430\u0447\u0435\u0441\u0442\u0432\u043e\s+\u0432\u043e\u0437\u0434\u0443\u0445\u0430$",
                "weather_aqi",
                310,
            ),
            (
                r"^\u044f\u043a\u0456\u0441\u0442\u044c\s+\u043f\u043e\u0432\u0456\u0442\u0440\u044f$",
                "weather_aqi",
                310,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        key = self._api_key()
        if key is None:
            return "Weather Extended is not connected, sir."
        try:
            import httpx  # noqa: E402
            async with httpx.AsyncClient(timeout=10) as client:
                if tool_name == "weather_week":
                    return await self._week(client, key, args)
                if tool_name == "weather_alerts":
                    return await self._alerts(client, key, args)
                if tool_name == "weather_aqi":
                    return await self._aqi(client, key, args)
        except Exception as e:
            log.warning("[weather_ext] %s failed", tool_name, exc_info=True)
            return f"Weather error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _geocode(self, client, key: str, city: str) -> tuple[float, float] | None:
        r = await client.get(
            f"{_OWM_API}/geo/1.0/direct",
            params={"q": city, "limit": 1, "appid": key},
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])

    async def _week(self, client, key: str, args: dict) -> str:
        city = args.get("city", "Kyiv")
        geo = await self._geocode(client, key, city)
        if not geo:
            return f"City '{city}' not found, sir."
        lat, lon = geo
        r = await client.get(
            f"{_OWM_API}/data/3.0/onecall",
            params={"lat": lat, "lon": lon, "exclude": "minutely,hourly",
                    "units": "metric", "appid": key},
        )
        r.raise_for_status()
        daily = r.json().get("daily", [])[:7]
        if not daily:
            return f"No forecast data for {city}, sir."
        lines = []
        for d in daily:
            temp_min = d["temp"]["min"]
            temp_max = d["temp"]["max"]
            desc = d["weather"][0]["description"] if d.get("weather") else ""
            lines.append(f"  \u2022 {temp_min:.0f}\u2013{temp_max:.0f}\u00b0C, {desc}")
        return f"7-day forecast for {city}, sir:\n" + "\n".join(lines)

    async def _alerts(self, client, key: str, args: dict) -> str:
        lat = args.get("lat", 50.45)
        lon = args.get("lon", 30.52)
        r = await client.get(
            f"{_OWM_API}/data/3.0/onecall",
            params={"lat": lat, "lon": lon, "exclude": "minutely,hourly,daily",
                    "appid": key},
        )
        r.raise_for_status()
        alerts = r.json().get("alerts", [])
        if not alerts:
            return "No active weather alerts, sir."
        lines = [f"  \u2022 {a.get('event', '?')}: {a.get('description', '')[:100]}"
                 for a in alerts[:5]]
        return "Weather alerts, sir:\n" + "\n".join(lines)

    async def _aqi(self, client, key: str, args: dict) -> str:
        city = args.get("city", "Kyiv")
        geo = await self._geocode(client, key, city)
        if not geo:
            return f"City '{city}' not found, sir."
        lat, lon = geo
        r = await client.get(
            f"{_OWM_API}/data/2.5/air_pollution",
            params={"lat": lat, "lon": lon, "appid": key},
        )
        r.raise_for_status()
        data = r.json()
        aqi_list = data.get("list", [])
        if not aqi_list:
            return f"No air quality data for {city}, sir."
        aqi = aqi_list[0]["main"]["aqi"]
        labels = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}
        label = labels.get(aqi, "Unknown")
        components = aqi_list[0].get("components", {})
        pm25 = components.get("pm2_5", "?")
        return f"Air quality in {city}: {label} (AQI {aqi}), PM2.5: {pm25}, sir."


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
