"""Converter — dedicated unit & currency conversion tool.

A standalone converter tool that handles unit and currency conversions,
separate from the calculator's math-expression evaluation. The calculator
handles "convert X to Y" as part of its multi-purpose expression parser;
this tool handles the natural-language *question* forms that don't start
with "convert":

  "how many km in 5 miles"
  "сколько км в 5 милях"
  "скільки км у 5 милях"
  "100 fahrenheit in celsius"
  "100 dollars to hryvnias" / "100 долларов в гривны" / "100 доларів у гривні"

Unit conversions are built-in (no internet).  Currency conversions fetch
rates from open.er-api.com (free, no key) and cache them for 1 hour.

Security: numeric parsing uses float(); no eval/exec.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unit conversion tables (built-in, no internet)
# ---------------------------------------------------------------------------

_UNIT_ALIASES: dict[str, str] = {
    # Length
    "km": "km", "kilometers": "km", "километров": "km", "км": "km",
    "кілометрів": "km", "kilometer": "km",
    "mi": "miles", "miles": "miles", "mile": "miles",
    "миль": "miles", "миля": "miles", "милях": "miles", "миле": "miles",
    "мілі": "miles", "міль": "miles", "милях": "miles",
    "m": "m", "meters": "m", "metres": "m", "метров": "m", "м": "m",
    "метрів": "m", "meter": "m", "metre": "m",
    "ft": "feet", "feet": "feet", "foot": "feet", "футов": "feet",
    "фут": "feet", "футів": "feet",
    "cm": "cm", "centimeters": "cm", "сантиметров": "cm", "см": "cm",
    "сантиметрів": "cm", "centimeter": "cm",
    "in": "inches", "inches": "inches", "inch": "inches",
    "дюймов": "inches", "дюйм": "inches", "дюймів": "inches",
    "yd": "yards", "yards": "yards", "yard": "yards",
    "ярдов": "yards", "ярд": "yards", "ярдів": "yards",
    # Weight
    "kg": "kg", "kilograms": "kg", "килограмм": "kg", "кг": "kg",
    "кілограм": "kg", "kilogram": "kg",
    "lbs": "lbs", "lb": "lbs", "pounds": "lbs", "pound": "lbs",
    "фунтов": "lbs", "фунт": "lbs", "фунтів": "lbs",
    "g": "g", "grams": "g", "грамм": "g", "г": "g", "грам": "g",
    "gram": "g",
    "oz": "oz", "ounces": "oz", "ounce": "oz",
    "унций": "oz", "унция": "oz", "унцій": "oz",
    # Volume
    "l": "l", "liters": "l", "litres": "l", "литров": "l", "л": "l",
    "літрів": "l", "liter": "l", "litre": "l",
    "gal": "gallons", "gallons": "gallons", "gallon": "gallons",
    "галлонов": "gallons", "галон": "gallons", "галонів": "gallons",
    "ml": "ml", "milliliters": "ml", "millilitres": "ml",
    "миллилитров": "ml", "мл": "ml", "мілілітрів": "ml",
    "milliliter": "ml", "millilitre": "ml",
    "fl_oz": "fl_oz", "fl oz": "fl_oz", "fluid ounces": "fl_oz",
    "fluid ounce": "fl_oz",
    # Data
    "b": "b", "bytes": "b", "байт": "b", "byte": "b",
    "kb": "kb", "kilobytes": "kb", "кб": "kb", "kilobyte": "kb",
    "mb": "mb", "megabytes": "mb", "мб": "mb", "megabyte": "mb",
    "gb": "gb", "gigabytes": "gb", "гб": "gb", "gigabyte": "gb",
    "tb": "tb", "terabytes": "tb", "тб": "tb", "terabyte": "tb",
    # Time
    "s": "seconds", "sec": "seconds", "seconds": "seconds",
    "second": "seconds", "секунд": "seconds", "сек": "seconds",
    "min": "minutes", "minutes": "minutes", "minute": "minutes",
    "минут": "minutes", "мин": "minutes", "хвилин": "minutes",
    "хв": "minutes",
    "h": "hours", "hr": "hours", "hours": "hours", "hour": "hours",
    "часов": "hours", "час": "hours", "годин": "hours",
}

# Canonical unit → base unit of dimension, and factor to base.
_UNIT_TO_BASE: dict[str, tuple[str, float]] = {
    # Length → meters
    "km": ("length_m", 1000.0),
    "miles": ("length_m", 1609.344),
    "m": ("length_m", 1.0),
    "feet": ("length_m", 0.3048),
    "cm": ("length_m", 0.01),
    "inches": ("length_m", 0.0254),
    "yards": ("length_m", 0.9144),
    # Weight → grams
    "kg": ("weight_g", 1000.0),
    "lbs": ("weight_g", 453.592),
    "g": ("weight_g", 1.0),
    "oz": ("weight_g", 28.3495),
    # Volume → liters
    "l": ("volume_l", 1.0),
    "gallons": ("volume_l", 3.78541),
    "ml": ("volume_l", 0.001),
    "fl_oz": ("volume_l", 0.0295735),
    # Data → bytes
    "b": ("data_b", 1.0),
    "kb": ("data_b", 1024.0),
    "mb": ("data_b", 1024.0 ** 2),
    "gb": ("data_b", 1024.0 ** 3),
    "tb": ("data_b", 1024.0 ** 4),
    # Time → seconds
    "seconds": ("time_s", 1.0),
    "minutes": ("time_s", 60.0),
    "hours": ("time_s", 3600.0),
}

# Temperature aliases
_TEMP_ALIASES: dict[str, str] = {
    "c": "C", "celsius": "C", "цельсия": "C", "цельсій": "C", "°c": "C",
    "f": "F", "fahrenheit": "F", "фаренгейта": "F", "фаренгейт": "F", "°f": "F",
    "k": "K", "kelvin": "K", "кельвин": "K", "°k": "K",
}


def _normalize_unit(raw: str) -> str | None:
    """Resolve a user-supplied unit string to canonical form."""
    key = raw.lower().strip().rstrip(".")
    return _UNIT_ALIASES.get(key)


def convert_units(value: float, from_raw: str, to_raw: str) -> float | None:
    """Convert between compatible units. Returns None if incompatible."""
    from_unit = _normalize_unit(from_raw)
    to_unit = _normalize_unit(to_raw)
    if from_unit is None or to_unit is None:
        return None
    from_info = _UNIT_TO_BASE.get(from_unit)
    to_info = _UNIT_TO_BASE.get(to_unit)
    if from_info is None or to_info is None:
        return None
    from_dim, from_factor = from_info
    to_dim, to_factor = to_info
    if from_dim != to_dim:
        return None
    base_value = value * from_factor
    return base_value / to_factor


def convert_temperature(value: float, from_raw: str, to_raw: str) -> float | None:
    """Convert between C, F, K. Returns None if not a temperature pair."""
    fr = _TEMP_ALIASES.get(from_raw.lower().strip())
    to = _TEMP_ALIASES.get(to_raw.lower().strip())
    if fr is None or to is None:
        return None
    if fr == to:
        return value
    # Convert to Celsius first
    if fr == "F":
        c = (value - 32) * 5 / 9
    elif fr == "K":
        c = value - 273.15
    else:
        c = value
    # Convert from Celsius to target
    if to == "F":
        return c * 9 / 5 + 32
    elif to == "K":
        return c + 273.15
    return c


# ---------------------------------------------------------------------------
# Currency conversion (requires internet)
# ---------------------------------------------------------------------------

_CURRENCY_CACHE: dict[str, tuple[float, dict[str, float]]] = {}
_CACHE_TTL = 3600  # 1 hour

_CURRENCY_ALIASES: dict[str, str] = {
    # EN
    "dollars": "USD", "dollar": "USD", "usd": "USD", "$": "USD",
    "euros": "EUR", "euro": "EUR", "eur": "EUR", "€": "EUR",
    "pounds": "GBP", "pound": "GBP", "gbp": "GBP", "£": "GBP",
    "yen": "JPY", "jpy": "JPY", "¥": "JPY",
    "yuan": "CNY", "cny": "CNY",
    "won": "KRW", "krw": "KRW",
    "bitcoin": "BTC", "btc": "BTC",
    "cad": "CAD", "aud": "AUD", "chf": "CHF", "inr": "INR",
    "brl": "BRL", "try": "TRY", "czk": "CZK",
    # RU
    "долларов": "USD", "доллар": "USD", "баксов": "USD",
    "евро": "EUR",
    "рублей": "RUB", "рубль": "RUB", "рубли": "RUB", "rub": "RUB",
    "гривен": "UAH", "гривень": "UAH", "грн": "UAH", "гривны": "UAH",
    "гривна": "UAH", "uah": "UAH", "hryvnias": "UAH", "hryvnia": "UAH",
    "злотых": "PLN", "злотые": "PLN", "злотый": "PLN", "pln": "PLN",
    "злоті": "PLN", "злотий": "PLN",
    "йен": "JPY",
    "юаней": "CNY",
    "биткоин": "BTC",
    # UK
    "доларів": "USD", "долар": "USD",
    "гривень": "UAH", "гривні": "UAH",
    "злотих": "PLN",
    "фунтов": "GBP", "фунтів": "GBP",
}


def normalize_currency(raw: str) -> str | None:
    """Resolve a spoken/typed currency name to ISO code."""
    key = raw.lower().strip()
    code = _CURRENCY_ALIASES.get(key)
    if code:
        return code
    upper = raw.upper().strip()
    if re.match(r"^[A-Z]{3}$", upper):
        return upper
    return None


async def fetch_rates(base: str) -> dict[str, float] | None:
    """Fetch exchange rates from open.er-api.com (free, no key)."""
    now = time.monotonic()
    cached = _CURRENCY_CACHE.get(base)
    if cached is not None:
        ts, rates = cached
        if now - ts < _CACHE_TTL:
            return rates
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://open.er-api.com/v6/latest/{base}"
            r = await client.get(url, timeout=5.0)
            r.raise_for_status()
            data = r.json()
            if data.get("result") != "success":
                return None
            rates = data.get("rates", {})
            _CURRENCY_CACHE[base] = (now, rates)
            return rates
    except Exception as exc:
        log.warning("currency fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt(val: float) -> str:
    """Format a number nicely: strip trailing zeros, limit precision."""
    if val == int(val) and abs(val) < 1e15:
        return f"{int(val):,}".replace(",", " ")
    formatted = f"{val:,.6f}".rstrip("0").rstrip(".")
    return formatted.replace(",", " ")


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_CYR = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_UK_CHARS = re.compile(r"[іїєґ]", re.IGNORECASE)


def _detect_lang(text: str) -> str:
    """Detect language from text: 'en', 'ru', or 'uk'."""
    if _CYR.search(text):
        if _UK_CHARS.search(text):
            return "uk"
        uk_words = {"від", "буде", "скільки", "конвертуй", "у"}
        words = set(re.findall(r"[а-яёіїєґ']+", text.lower()))
        if words & uk_words:
            return "uk"
        return "ru"
    return "en"


# ---------------------------------------------------------------------------
# Regex for parsing "how many X in Y" style queries
# ---------------------------------------------------------------------------

# EN: "how many km in 5 miles", "100 fahrenheit in celsius"
_RE_HOW_MANY_EN = re.compile(
    r"^how\s+many\s+(\S+)\s+in\s+(\d+(?:\.\d+)?)\s+(\S+)$",
    re.IGNORECASE,
)

# EN: "100 dollars to hryvnias", "100 fahrenheit to celsius"
_RE_VALUE_TO_EN = re.compile(
    r"^(\d+(?:\.\d+)?)\s+(\S+)\s+(?:to|in)\s+(\S+)$",
    re.IGNORECASE,
)

# RU: "сколько км в 5 милях", "переведи 100 долларов в гривны"
_RE_HOW_MANY_RU = re.compile(
    r"^сколько\s+(\S+)\s+в\s+(\d+(?:[.,]\d+)?)\s+(\S+)$",
    re.IGNORECASE,
)
_RE_VALUE_TO_RU = re.compile(
    r"^(?:переведи|конвертируй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+в\s+(\S+)$",
    re.IGNORECASE,
)

# UK: "скільки км у 5 милях", "переведи 100 доларів у гривні"
_RE_HOW_MANY_UK = re.compile(
    r"^скільки\s+(\S+)\s+(?:в|у)\s+(\d+(?:[.,]\d+)?)\s+(\S+)$",
    re.IGNORECASE,
)
_RE_VALUE_TO_UK = re.compile(
    r"^(?:переведи|конвертуй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+(?:в|у)\s+(\S+)$",
    re.IGNORECASE,
)


def _parse_number(s: str) -> float:
    """Parse a number that may use comma as decimal separator."""
    return float(s.replace(",", "."))


# ---------------------------------------------------------------------------
# Response formatters — language-aware
# ---------------------------------------------------------------------------

def _conv_response(
    lang: str, value: float, from_u: str, result: float, to_u: str,
) -> str:
    v = _fmt(value)
    r = _fmt(result)
    if lang == "ru":
        return f"{v} {from_u} = {r} {to_u}, сэр."
    if lang == "uk":
        return f"{v} {from_u} = {r} {to_u}, сер."
    return f"{v} {from_u} = {r} {to_u}, sir."


def _how_many_response(
    lang: str, value: float, from_u: str, result: float, to_u: str,
) -> str:
    v = _fmt(value)
    r = _fmt(result)
    if lang == "ru":
        return f"В {v} {from_u} — {r} {to_u}, сэр."
    if lang == "uk":
        return f"У {v} {from_u} — {r} {to_u}, сер."
    return f"{v} {from_u} = {r} {to_u}, sir."


def _no_internet_msg(lang: str) -> str:
    if lang == "ru":
        return "Мне нужен интернет для конвертации валют, сэр."
    if lang == "uk":
        return "Мені потрібен інтернет для конвертації валют, сер."
    return "I need internet for currency conversion, sir."


def _unknown_currency_msg(lang: str, code: str) -> str:
    if lang == "ru":
        return f"Неизвестная валюта: {code}"
    if lang == "uk":
        return f"Невідома валюта: {code}"
    return f"Unknown currency: {code}"


def _unknown_units_msg(lang: str, from_u: str, to_u: str) -> str:
    if lang == "ru":
        return f"Не знаю как конвертировать {from_u} в {to_u}, сэр."
    if lang == "uk":
        return f"Не знаю як конвертувати {from_u} в {to_u}, сер."
    return f"I don't know how to convert {from_u} to {to_u}, sir."


def _err_msg(lang: str) -> str:
    if lang == "ru":
        return "Не удалось распознать что конвертировать, сэр."
    if lang == "uk":
        return "Не вдалося розпізнати що конвертувати, сер."
    return "Could not parse the conversion request, sir."


# ---------------------------------------------------------------------------
# Core conversion logic
# ---------------------------------------------------------------------------

async def _do_convert(
    value: float,
    from_unit: str,
    to_unit: str,
    lang: str,
) -> ToolResult:
    """Attempt temperature → unit → currency conversion in order."""
    # Temperature first (special offset math)
    temp = convert_temperature(value, from_unit, to_unit)
    if temp is not None:
        return ToolResult(
            success=True,
            output=_conv_response(lang, value, from_unit, temp, to_unit),
        )

    # Standard unit conversion
    converted = convert_units(value, from_unit, to_unit)
    if converted is not None:
        return ToolResult(
            success=True,
            output=_conv_response(lang, value, from_unit, converted, to_unit),
        )

    # Currency conversion
    from_cur = normalize_currency(from_unit)
    to_cur = normalize_currency(to_unit)
    if from_cur and to_cur:
        rates = await fetch_rates(from_cur)
        if rates is None:
            return ToolResult(success=False, error=_no_internet_msg(lang))
        rate = rates.get(to_cur)
        if rate is None:
            return ToolResult(
                success=False,
                error=_unknown_currency_msg(lang, to_cur),
            )
        converted_val = value * rate
        return ToolResult(
            success=True,
            output=_conv_response(lang, value, from_cur, converted_val, to_cur),
        )

    return ToolResult(
        success=False,
        error=_unknown_units_msg(lang, from_unit, to_unit),
    )


async def _do_how_many(
    to_unit: str,
    value: float,
    from_unit: str,
    lang: str,
) -> ToolResult:
    """Handle 'how many X in Y Z' — note reversed order."""
    # Temperature
    temp = convert_temperature(value, from_unit, to_unit)
    if temp is not None:
        return ToolResult(
            success=True,
            output=_how_many_response(lang, value, from_unit, temp, to_unit),
        )

    # Standard units
    converted = convert_units(value, from_unit, to_unit)
    if converted is not None:
        return ToolResult(
            success=True,
            output=_how_many_response(lang, value, from_unit, converted, to_unit),
        )

    return ToolResult(
        success=False,
        error=_unknown_units_msg(lang, from_unit, to_unit),
    )


# ---------------------------------------------------------------------------
# ConverterTool
# ---------------------------------------------------------------------------

class ConverterArgs(BaseModel):
    query: str


class ConverterTool:
    """Dedicated unit and currency converter.

    Handles natural-language conversion requests:
      • "how many km in 5 miles" / "сколько км в 5 милях"
      • "100 dollars to hryvnias" / "100 долларов в гривны"
      • "100 fahrenheit in celsius"

    Units (built-in, no internet):
      Length  km↔miles, m↔feet, cm↔inches, m↔yards
      Weight  kg↔lbs, g↔oz
      Temp    C↔F↔K
      Volume  L↔gallons, ml↔fl_oz
      Data    KB↔MB↔GB↔TB
      Time    h↔min↔sec

    Currency (internet, cached 1h):
      Rates from open.er-api.com (free, no key).
    """

    name: str = "converter"
    description: str = (
        "Convert between units or currencies. "
        "Use for: 'how many km in 5 miles', '100 dollars to hryvnias', "
        "'100 fahrenheit in celsius', 'сколько км в 5 милях', "
        "'переведи 100 долларов в гривны', 'скільки км у 5 милях'. "
        "Built-in units: length, weight, temperature, volume, data, time. "
        "Currency via internet (cached 1h). "
        "(RU: сколько X в Y / переведи / UK: скільки X у Y / конвертуй)"
    )
    args_schema: type[BaseModel] = ConverterArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ

    # ── voice patterns ────────────────────────────────────────────────
    # Priority 84: one tick above calculator's 85 (lower = higher prio)
    # so the dedicated converter catches the question-form patterns
    # ("how many", "сколько X в Y") before the calculator's catch-all.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "how many km in 5 miles"
        VoicePattern(
            regex=r"^how\s+many\s+(\S+)\s+in\s+(\d+(?:\.\d+)?)\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
        # EN: "100 dollars to hryvnias", "100 fahrenheit in celsius"
        VoicePattern(
            regex=r"^(\d+(?:\.\d+)?)\s+(\S+)\s+(?:to|in)\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
        # RU: "сколько км в 5 милях"
        VoicePattern(
            regex=r"^сколько\s+(\S+)\s+в\s+(\d+(?:[.,]\d+)?)\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
        # RU: "переведи 100 долларов в гривны"
        VoicePattern(
            regex=r"^(?:переведи|конвертируй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+в\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
        # UK: "скільки км у 5 милях"
        VoicePattern(
            regex=r"^скільки\s+(\S+)\s+(?:в|у)\s+(\d+(?:[.,]\d+)?)\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
        # UK: "переведи 100 доларів у гривні"
        VoicePattern(
            regex=r"^(?:переведи|конвертуй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+(?:в|у)\s+(\S+)$",
            priority=84,
            args=lambda m: {"query": m.group(0)},
        ),
    )

    async def execute(self, args: ConverterArgs) -> ToolResult:
        query = args.query.strip()
        lang = _detect_lang(query)

        # 1. Try "how many X in Y Z" patterns
        for pattern in (_RE_HOW_MANY_EN, _RE_HOW_MANY_RU, _RE_HOW_MANY_UK):
            m = pattern.match(query)
            if m:
                to_unit = m.group(1)
                value = _parse_number(m.group(2))
                from_unit = m.group(3)
                return await _do_how_many(to_unit, value, from_unit, lang)

        # 2. Try "N from to" patterns
        for pattern in (_RE_VALUE_TO_EN, _RE_VALUE_TO_RU, _RE_VALUE_TO_UK):
            m = pattern.match(query)
            if m:
                value = _parse_number(m.group(1))
                from_unit = m.group(2)
                to_unit = m.group(3)
                return await _do_convert(value, from_unit, to_unit, lang)

        return ToolResult(success=False, error=_err_msg(lang))
