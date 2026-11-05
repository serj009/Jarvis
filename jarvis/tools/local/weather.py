"""Current weather conditions via Open-Meteo (no API key required).

Location resolution order when no explicit location arg is given:
  1. cfg.weather.latitude / longitude — user-configured or previously
     persisted from IP detection; highest priority.
  2. ipapi.co auto-detect — used only when both coords are None. Result
     is written back to cfg.weather and persisted via save_fn so the next
     app launch skips ipapi.co entirely.

When a location string arg is provided:
  - Geocodes via Open-Meteo's free geocoding API (no key required).
  - On success: uses those coords and includes the city name in the response.
  - On failure (no results, timeout, network error): falls back to configured
    default with a spoken acknowledgment.

Open-Meteo API: https://open-meteo.com/
Open-Meteo Geocoding: https://open-meteo.com/en/docs/geocoding-api
WMO weather codes: https://open-meteo.com/en/docs/wmo-weather-code
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar

import httpx
from pydantic import BaseModel

from jarvis.core.phrases import Lang, reply_language, say
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# WMO weather interpretation codes (Open-Meteo / WMO standard).
_WMO: dict[int, str] = {
    0:  "clear skies",
    1:  "mainly clear",
    2:  "partly cloudy",
    3:  "overcast",
    45: "foggy",
    48: "foggy with rime",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "moderate showers",
    82: "heavy showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

# The same codes in Russian and Ukrainian (B-P1-9: the answer was English
# text that the model then retold).
_WMO_RU: dict[int, str] = {
    0:  "ясно",
    1:  "преимущественно ясно",
    2:  "переменная облачность",
    3:  "пасмурно",
    45: "туман",
    48: "туман с изморозью",
    51: "лёгкая морось",
    53: "умеренная морось",
    55: "сильная морось",
    56: "лёгкая ледяная морось",
    57: "сильная ледяная морось",
    61: "небольшой дождь",
    63: "умеренный дождь",
    65: "сильный дождь",
    66: "небольшой ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "умеренный снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "небольшой ливень",
    81: "умеренный ливень",
    82: "сильный ливень",
    85: "небольшой снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с сильным градом",
}

_WMO_UK: dict[int, str] = {
    0:  "ясно",
    1:  "переважно ясно",
    2:  "мінлива хмарність",
    3:  "похмуро",
    45: "туман",
    48: "туман з памороззю",
    51: "легка мряка",
    53: "помірна мряка",
    55: "сильна мряка",
    56: "легка крижана мряка",
    57: "сильна крижана мряка",
    61: "невеликий дощ",
    63: "помірний дощ",
    65: "сильний дощ",
    66: "невеликий крижаний дощ",
    67: "сильний крижаний дощ",
    71: "невеликий сніг",
    73: "помірний сніг",
    75: "сильний сніг",
    77: "снігова крупа",
    80: "невелика злива",
    81: "помірна злива",
    82: "сильна злива",
    85: "невеликий снігопад",
    86: "сильний снігопад",
    95: "гроза",
    96: "гроза з градом",
    99: "гроза з сильним градом",
}

_CONDITIONS: dict[Lang, dict[int, str]] = {"en": _WMO, "ru": _WMO_RU, "uk": _WMO_UK}
_UNKNOWN_CONDITIONS: dict[Lang, str] = {
    "en": "unknown conditions",
    "ru": "состояние неба неизвестно",
    "uk": "стан неба невідомий",
}

# (one, few, many) forms of "degree"; English uses (one, many, many).
_DEGREE_FORMS: dict[Lang, tuple[str, str, str]] = {
    "en": ("degree", "degrees", "degrees"),
    "ru": ("градус", "градуса", "градусов"),
    "uk": ("градус", "градуси", "градусів"),
}
_SCALE_NAMES: dict[Lang, dict[str, str]] = {
    "en": {"celsius": "Celsius", "fahrenheit": "Fahrenheit"},
    "ru": {"celsius": "по Цельсию", "fahrenheit": "по Фаренгейту"},
    "uk": {"celsius": "за Цельсієм", "fahrenheit": "за Фаренгейтом"},
}
_MINUS: dict[Lang, str] = {"en": "minus", "ru": "минус", "uk": "мінус"}


def _degree_word(n: int, lang: Lang) -> str:
    one, few, many = _DEGREE_FORMS[lang]
    if lang == "en":
        return one if n == 1 else many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _spoken_temperature(temp: float, unit: str, lang: Lang) -> str:
    """"12 градусов по Цельсию", "minus 3 degrees Celsius".

    The scale is always named: "86 degrees" alone did not say it was
    Fahrenheit. "minus" is a word because the voices may skip a "-"."""
    rounded = round(temp)
    n = abs(rounded)
    sign = f"{_MINUS[lang]} " if rounded < 0 else ""
    scale = _SCALE_NAMES[lang].get(unit, _SCALE_NAMES[lang]["celsius"])
    return f"{sign}{n} {_degree_word(n, lang)} {scale}"


def _conditions(code: int, lang: Lang) -> str:
    return _CONDITIONS[lang].get(code, _UNKNOWN_CONDITIONS[lang])


_IPAPI_URL = "https://ipapi.co/json/"
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_TIMEOUT = 3.0
_GEO_TIMEOUT = 5.0


def _geocode_queries(location: str) -> list[str]:
    """Return candidate query strings to try in order.

    If the location contains a comma (e.g. "Reston, Virginia"), a second
    attempt with just the city name is appended so city+state inputs work
    even when the geocoding API returns no results for the full string.
    """
    queries = [location]
    if "," in location:
        city_only = location.split(",", 1)[0].strip()
        if city_only:
            queries.append(city_only)
    return queries


class WeatherArgs(BaseModel):
    location: str | None = None


class WeatherTool:
    name: str = "get_weather"
    description: str = (
        "Reports current weather conditions. Optionally accepts a 'location' "
        "argument (city name or city+state). Without it, uses the configured "
        "default location. Use this whenever the user asks about weather, "
        "temperature, forecast, rain, snow, or similar weather topics. "
        "(RU: погода / UK: погода)"
    )
    args_schema: type[BaseModel] = WeatherArgs
    requires_confirmation: bool = False

    # Voice patterns for deterministic dispatch — skip LLM for weather.
    #
    # Priority 90: the highest priority among these new tool patterns.
    # Weather queries are short, exact-matchable, have no parameters,
    # and are among the most common daily commands.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "weather", "what's the weather", "what is the weather"
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+the\s+)?weather$",
            priority=90,
        ),
        # EN: "how's the weather", "how is the weather"
        VoicePattern(
            regex=r"^how(?:'s|\s+is)\s+the\s+weather$",
            priority=90,
        ),
        # RU: "погода", "какая погода"
        VoicePattern(
            regex=r"^(?:какая\s+)?погод[аеу]$",
            priority=90,
        ),
        # RU: "что с погодой", "что там на погоде"
        VoicePattern(
            regex=r"^что\s+(?:там\s+)?(?:с|на)\s+погод(?:ой|е)$",
            priority=90,
        ),
        # UK: "погода", "яка погода"
        VoicePattern(
            regex=r"^(?:яка\s+)?погод[аі]$",
            priority=90,
        ),
        # UK: "що з погодою", "що там на погоді"
        VoicePattern(
            regex=r"^що\s+(?:там\s+)?(?:з|на)\s+погод(?:ою|і)$",
            priority=90,
        ),
    )

    def __init__(
        self,
        *,
        weather_config: object,
        save_fn: Callable[[], None] | None = None,
    ) -> None:
        self._config = weather_config
        # Called after IP-detection to persist coords so the next run skips ipapi.co.
        self._save_fn = save_fn

    async def execute(self, args: WeatherArgs) -> ToolResult:
        try:
            async with httpx.AsyncClient() as client:
                if args.location is not None:
                    geo = await self._geocode(client, args.location)
                    if geo is None:
                        lat, lon = await self._resolve_location(client)
                        result = await self._fetch_weather(client, lat, lon)
                        if result.success and result.output:
                            not_found = say(
                                "weather_city_not_found", location=args.location
                            )
                            return ToolResult(
                                success=True,
                                output=f"{not_found} {result.output}",
                            )
                        return result
                    lat, lon, city_name = geo
                    return await self._fetch_weather(client, lat, lon, city=city_name)
                lat, lon = await self._resolve_location(client)
                return await self._fetch_weather(client, lat, lon)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            log.warning("weather: no network: %s", exc)
            return ToolResult(success=False, output=say("weather_no_network"))
        except Exception as exc:
            log.warning("weather fetch failed: %s", exc)
            return ToolResult(success=False, output=say("weather_unreachable"))

    async def _geocode(
        self, client: httpx.AsyncClient, location: str
    ) -> tuple[float, float, str] | None:
        """Return (lat, lon, city_name) from the geocoding API, or None on failure.

        Tries the full query first. If no results and the query contains a
        comma (e.g. "Reston, Virginia"), retries with just the part before
        the first comma so city+state formats work reliably.
        """
        for query in _geocode_queries(location):
            try:
                r = await client.get(
                    _GEOCODING_URL,
                    params={"name": query, "count": 1},
                    timeout=_GEO_TIMEOUT,
                )
                r.raise_for_status()
                data = r.json()
                results = data.get("results") or []
                if results:
                    first = results[0]
                    return (
                        float(first["latitude"]),
                        float(first["longitude"]),
                        str(first["name"]),
                    )
                log.info("geocoding: no results for %r", query)
            except Exception as exc:
                log.warning("geocoding failed for %r: %s", query, exc)
                return None  # don't retry after a network error
        return None

    async def _resolve_location(self, client: httpx.AsyncClient) -> tuple[float, float]:
        cfg_lat = getattr(self._config, "latitude", None)
        cfg_lon = getattr(self._config, "longitude", None)
        if cfg_lat is not None and cfg_lon is not None:
            return float(cfg_lat), float(cfg_lon)
        log.info("weather: auto-detecting location from IP via ipapi.co")
        r = await client.get(_IPAPI_URL, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        lat = float(data["latitude"])
        lon = float(data["longitude"])
        # Persist so subsequent runs skip ipapi.co.
        try:
            self._config.latitude = lat  # type: ignore[union-attr]
            self._config.longitude = lon  # type: ignore[union-attr]
            if self._save_fn is not None:
                self._save_fn()
        except Exception:
            log.warning("weather: could not persist detected location")
        return lat, lon

    async def _fetch_weather(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        *,
        city: str | None = None,
    ) -> ToolResult:
        unit = getattr(self._config, "unit", "celsius")
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "temperature_unit": unit,
        }
        r = await client.get(_OPEN_METEO_URL, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        current = data["current"]
        lang = reply_language()
        temp = _spoken_temperature(float(current["temperature_2m"]), unit, lang)
        conditions = _conditions(int(current["weather_code"]), lang)
        if city is not None:
            return ToolResult(
                success=True,
                output=say("weather_now_in", lang, temp=temp, conditions=conditions, city=city),
            )
        return ToolResult(
            success=True,
            output=say("weather_now", lang, temp=temp, conditions=conditions),
        )
