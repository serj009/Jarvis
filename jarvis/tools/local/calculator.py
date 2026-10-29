"""Calculator + Converter — instant math, percentages, and unit/currency conversions.

"Сколько будет 15% от 3500?" — a dedicated calculator tool gives an instant
answer without routing through the LLM.

Supported operations:
  • Math expressions: +, -, *, /, %, **, (), sqrt, sin, cos, tan, log, abs
  • Percentages: "15% of 3500" → 525
  • Unit conversion (built-in, no internet):
      Length   km↔miles, m↔feet, cm↔inches
      Weight   kg↔lbs, g↔oz
      Temp     C↔F↔K
      Volume   L↔gallons
      Data     MB↔GB↔TB
      Time     hours↔minutes↔seconds
  • Currency (requires internet):
      Fetch rates from open.er-api.com (free, no key)
      Cache rates for 1 hour
      Fallback: spoken "I need internet" message

Security: uses ast.parse to whitelist only safe math operations.  No exec,
no eval of arbitrary code.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
import time
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safe math evaluator via ast.parse
# ---------------------------------------------------------------------------

_SAFE_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node using only safe operations."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant: {node.value!r}")

    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
        return op_fn(_safe_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        return op_fn(left, right)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        name = node.func.id
        fn = _SAFE_FUNCS.get(name)
        if fn is None:
            raise ValueError(f"Unknown function: {name}")
        if callable(fn):
            evaluated_args = [_safe_eval_node(a) for a in node.args]
            return float(fn(*evaluated_args))
        raise ValueError(f"{name} is a constant, not a function")

    if isinstance(node, ast.Name):
        val = _SAFE_FUNCS.get(node.id)
        if val is not None and not callable(val):
            return float(val)
        raise ValueError(f"Unknown name: {node.id}")

    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def safe_math_eval(expr: str) -> float:
    """Evaluate a math expression safely using ast.parse.

    Raises ValueError on unsupported/malicious input.
    """
    # Normalize: replace × with *, ÷ with /, ^ with **
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    tree = ast.parse(expr, mode="eval")
    return _safe_eval_node(tree)


# ---------------------------------------------------------------------------
# Unit conversion tables (built-in, no internet)
# ---------------------------------------------------------------------------

# Conversion factors to a canonical unit within each dimension.
# Format: {unit_alias: (canonical_unit, multiplier)}
# value_in_canonical = value * multiplier

_UNIT_ALIASES: dict[str, str] = {
    # Length
    "km": "km",
    "kilometers": "km",
    "километров": "km",
    "км": "km",
    "кілометрів": "km",
    "mi": "miles",
    "miles": "miles",
    "миль": "miles",
    "миля": "miles",
    "мілі": "miles",
    "миле": "miles",
    "m": "m",
    "meters": "m",
    "metres": "m",
    "метров": "m",
    "м": "m",
    "метрів": "m",
    "ft": "feet",
    "feet": "feet",
    "foot": "feet",
    "футов": "feet",
    "фут": "feet",
    "футів": "feet",
    "cm": "cm",
    "centimeters": "cm",
    "сантиметров": "cm",
    "см": "cm",
    "сантиметрів": "cm",
    "in": "inches",
    "inches": "inches",
    "inch": "inches",
    "дюймов": "inches",
    "дюйм": "inches",
    "дюймів": "inches",
    # Weight
    "kg": "kg",
    "kilograms": "kg",
    "килограмм": "kg",
    "кг": "kg",
    "кілограм": "kg",
    "lbs": "lbs",
    "lb": "lbs",
    "pounds": "lbs",
    "фунтов": "lbs",
    "фунт": "lbs",
    "фунтів": "lbs",
    "g": "g",
    "grams": "g",
    "грамм": "g",
    "г": "g",
    "грам": "g",
    "oz": "oz",
    "ounces": "oz",
    "ounce": "oz",
    "унций": "oz",
    "унция": "oz",
    "унцій": "oz",
    # Volume
    "l": "l",
    "liters": "l",
    "litres": "l",
    "литров": "l",
    "л": "l",
    "літрів": "l",
    "gal": "gallons",
    "gallons": "gallons",
    "галлонов": "gallons",
    "галон": "gallons",
    "галонів": "gallons",
    # Data
    "b": "b",
    "bytes": "b",
    "байт": "b",
    "kb": "kb",
    "kilobytes": "kb",
    "кб": "kb",
    "mb": "mb",
    "megabytes": "mb",
    "мб": "mb",
    "gb": "gb",
    "gigabytes": "gb",
    "гб": "gb",
    "tb": "tb",
    "terabytes": "tb",
    "тб": "tb",
    # Time
    "s": "seconds",
    "sec": "seconds",
    "seconds": "seconds",
    "секунд": "seconds",
    "сек": "seconds",
    "min": "minutes",
    "minutes": "minutes",
    "минут": "minutes",
    "мин": "minutes",
    "хвилин": "minutes",
    "хв": "minutes",
    "h": "hours",
    "hr": "hours",
    "hours": "hours",
    "часов": "hours",
    "час": "hours",
    "годин": "hours",
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
    # Weight → grams
    "kg": ("weight_g", 1000.0),
    "lbs": ("weight_g", 453.592),
    "g": ("weight_g", 1.0),
    "oz": ("weight_g", 28.3495),
    # Volume → liters
    "l": ("volume_l", 1.0),
    "gallons": ("volume_l", 3.78541),
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


# Temperature is special (offset conversions, not just multipliers).

def _convert_temperature(value: float, from_raw: str, to_raw: str) -> float | None:
    """Convert between C, F, K. Returns None if not a temperature pair."""
    temp_aliases: dict[str, str] = {
        "c": "C", "celsius": "C", "цельсия": "C", "цельсій": "C", "°c": "C",
        "f": "F", "fahrenheit": "F", "фаренгейта": "F", "фаренгейт": "F", "°f": "F",
        "k": "K", "kelvin": "K", "кельвин": "K", "°k": "K",
    }
    fr = temp_aliases.get(from_raw.lower().strip())
    to = temp_aliases.get(to_raw.lower().strip())
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
    # Common spoken aliases → ISO 4217
    "dollars": "USD", "dollar": "USD", "usd": "USD", "$": "USD",
    "долларов": "USD", "доларів": "USD", "баксов": "USD",
    "euros": "EUR", "euro": "EUR", "eur": "EUR", "€": "EUR",
    "евро": "EUR",
    "pounds": "GBP", "pound": "GBP", "gbp": "GBP", "£": "GBP",
    "фунтов": "GBP", "фунтів": "GBP",
    "yen": "JPY", "jpy": "JPY", "¥": "JPY", "йен": "JPY",
    "rubles": "RUB", "rub": "RUB", "рублей": "RUB", "рубль": "RUB",
    "hryvnia": "UAH", "uah": "UAH", "гривен": "UAH", "гривень": "UAH", "грн": "UAH",
    "zloty": "PLN", "pln": "PLN", "злотых": "PLN", "злотих": "PLN",
    "yuan": "CNY", "cny": "CNY", "юаней": "CNY",
    "won": "KRW", "krw": "KRW",
    "bitcoin": "BTC", "btc": "BTC", "биткоин": "BTC",
    "cad": "CAD", "aud": "AUD", "chf": "CHF", "inr": "INR",
    "brl": "BRL", "try": "TRY", "czk": "CZK",
}


def _normalize_currency(raw: str) -> str | None:
    """Resolve a spoken/typed currency name to ISO code."""
    key = raw.lower().strip()
    # Try direct alias lookup first
    code = _CURRENCY_ALIASES.get(key)
    if code:
        return code
    # If it's already 3 uppercase letters, accept as-is
    upper = raw.upper().strip()
    if re.match(r"^[A-Z]{3}$", upper):
        return upper
    return None


async def _fetch_rates(base: str) -> dict[str, float] | None:
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
# Regex helpers for parsing user input
# ---------------------------------------------------------------------------

_RE_PERCENT_EN = re.compile(
    r"^(?:what(?:'s|\s+is)\s+)?(\d+(?:\.\d+)?)\s*(?:percent|%)\s+(?:of|from)\s+(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_RE_PERCENT_RU = re.compile(
    r"^(?:сколько\s+(?:будет\s+)?)?(\d+(?:[.,]\d+)?)\s*(?:процент(?:ов|а)?|%)\s+от\s+(\d+(?:[.,]\d+)?)$",
    re.IGNORECASE,
)
_RE_PERCENT_UK = re.compile(
    r"^(?:скільки\s+(?:буде\s+)?)?(\d+(?:[.,]\d+)?)\s*(?:відсотк(?:ів|и)?|%)\s+від\s+(\d+(?:[.,]\d+)?)$",
    re.IGNORECASE,
)
_RE_CONVERT_EN = re.compile(
    r"^(?:convert\s+)?(\d+(?:\.\d+)?)\s+(\S+)\s+(?:to|in)\s+(\S+)$",
    re.IGNORECASE,
)
_RE_CONVERT_RU = re.compile(
    r"^(?:переведи|конвертируй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+в\s+(\S+)$",
    re.IGNORECASE,
)
_RE_CONVERT_UK = re.compile(
    r"^(?:переведи|конвертуй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+(?:в|у)\s+(\S+)$",
    re.IGNORECASE,
)
_RE_MATH_EN = re.compile(
    r"^(?:(?:what(?:'s|\s+is)\s+)|(?:calculate|compute)\s+)?(.+)$",
    re.IGNORECASE,
)


def _parse_number(s: str) -> float:
    """Parse a number that may use comma as decimal separator."""
    return float(s.replace(",", "."))


# ---------------------------------------------------------------------------
# CalculatorTool
# ---------------------------------------------------------------------------


class CalculatorArgs(BaseModel):
    expression: str


class CalculatorTool:
    """Calculate math expressions, percentages, and unit/currency conversions."""

    name: str = "calculator"
    description: str = (
        "Calculate math expressions, percentages, and unit/currency conversions. "
        "Use this for any math question: '15 + 27', '15% of 3500', "
        "'convert 10 km to miles', '100 USD to EUR'. "
        "Supports +, -, *, /, **, sqrt, sin, cos, tan, log, abs. "
        "Built-in unit conversion for length, weight, temperature, volume, "
        "data sizes, and time. Currency conversion requires internet. "
        "(RU: посчитай, сколько будет / UK: порахуй, скільки буде)"
    )
    args_schema: type[BaseModel] = CalculatorArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ

    # ── voice patterns ────────────────────────────────────────────────
    # Priority 85: high (pure math), slightly below get_time (80) to
    # avoid shadowing time queries. Calculator patterns are distinctive
    # enough that priority 85 is safe.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "what's 15 + 27", "what is 100 / 5"
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+)?(\d[\d\s+\-*/%.()]+\d)\s*$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # EN: "calculate ...", "compute ..."
        VoicePattern(
            regex=r"^(?:calculate|compute)\s+(.+)$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # EN: "15 percent of 3500", "how much is 20% of 500"
        VoicePattern(
            regex=r"^(?:(?:what(?:'s|\s+is)\s+)|(?:how\s+much\s+is\s+))?(\d+(?:\.\d+)?)\s*(?:percent|%)\s+(?:of|from)\s+(\d+(?:\.\d+)?)$",
            priority=85,
            args=lambda m: {"expression": f"{m.group(1)}% of {m.group(2)}"},
        ),
        # EN: "convert 10 km to miles"
        VoicePattern(
            regex=r"^convert\s+(\d+(?:\.\d+)?)\s+(\S+)\s+to\s+(\S+)$",
            priority=85,
            args=lambda m: {"expression": f"convert {m.group(1)} {m.group(2)} to {m.group(3)}"},
        ),
        # RU: "сколько будет 15 + 27"
        VoicePattern(
            regex=r"^сколько\s+будет\s+(.+)$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # RU: "посчитай 100 * 5"
        VoicePattern(
            regex=r"^посчитай\s+(.+)$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # RU: "15 процентов от 3500"
        VoicePattern(
            regex=r"^(?:сколько\s+(?:будет\s+)?)?(\d+(?:[.,]\d+)?)\s*(?:процент(?:ов|а)?|%)\s+от\s+(\d+(?:[.,]\d+)?)$",
            priority=85,
            args=lambda m: {"expression": f"{m.group(1)}% от {m.group(2)}"},
        ),
        # RU: "переведи 10 км в мили"
        VoicePattern(
            regex=r"^(?:переведи|конвертируй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+в\s+(\S+)$",
            priority=85,
            args=lambda m: {"expression": f"переведи {m.group(1)} {m.group(2)} в {m.group(3)}"},
        ),
        # UK: "скільки буде 15 + 27"
        VoicePattern(
            regex=r"^скільки\s+буде\s+(.+)$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # UK: "порахуй 100 * 5"
        VoicePattern(
            regex=r"^порахуй\s+(.+)$",
            priority=85,
            args=lambda m: {"expression": m.group(1).strip()},
        ),
        # UK: "15 відсотків від 3500"
        VoicePattern(
            regex=r"^(?:скільки\s+(?:буде\s+)?)?(\d+(?:[.,]\d+)?)\s*(?:відсотк(?:ів|и)?|%)\s+від\s+(\d+(?:[.,]\d+)?)$",
            priority=85,
            args=lambda m: {"expression": f"{m.group(1)}% від {m.group(2)}"},
        ),
        # UK: "переведи 10 км в мілі"
        VoicePattern(
            regex=r"^(?:переведи|конвертуй)\s+(\d+(?:[.,]\d+)?)\s+(\S+)\s+(?:в|у)\s+(\S+)$",
            priority=85,
            args=lambda m: {"expression": f"переведи {m.group(1)} {m.group(2)} в {m.group(3)}"},
        ),
    )

    async def execute(self, args: CalculatorArgs) -> ToolResult:
        expr = args.expression.strip()
        lang = _detect_expr_lang(expr)

        # 1. Try percentage
        result = _try_percentage(expr, lang)
        if result is not None:
            return result

        # 2. Try unit/temperature conversion
        result = await _try_conversion(expr, lang)
        if result is not None:
            return result

        # 3. Try math expression
        result = _try_math(expr, lang)
        if result is not None:
            return result

        return ToolResult(
            success=False,
            error=_err_msg(lang, expr),
        )


# ---------------------------------------------------------------------------
# Language detection for the expression
# ---------------------------------------------------------------------------

_CYR = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_UK_CHARS = re.compile(r"[іїєґ]", re.IGNORECASE)


def _detect_expr_lang(expr: str) -> str:
    """Detect language from the expression text: 'en', 'ru', or 'uk'."""
    if _CYR.search(expr):
        if _UK_CHARS.search(expr):
            return "uk"
        # Check for UK-specific words without unique chars
        uk_words = {"від", "буде", "скільки", "порахуй", "конвертуй"}
        words = set(re.findall(r"[а-яёіїєґ']+", expr.lower()))
        if words & uk_words:
            return "uk"
        return "ru"
    return "en"


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------

def _try_percentage(expr: str, lang: str) -> ToolResult | None:
    """Try to parse and compute a percentage expression."""
    for pattern in (_RE_PERCENT_EN, _RE_PERCENT_RU, _RE_PERCENT_UK):
        m = pattern.match(expr)
        if m:
            pct = _parse_number(m.group(1))
            base = _parse_number(m.group(2))
            result = pct / 100.0 * base
            return ToolResult(
                success=True,
                output=_pct_response(lang, pct, base, result),
            )
    return None


async def _try_conversion(expr: str, lang: str) -> ToolResult | None:
    """Try to parse and execute a unit/currency conversion."""
    for pattern in (_RE_CONVERT_EN, _RE_CONVERT_RU, _RE_CONVERT_UK):
        m = pattern.match(expr)
        if m:
            value = _parse_number(m.group(1))
            from_unit = m.group(2)
            to_unit = m.group(3)

            # Temperature first (special offset math)
            temp = _convert_temperature(value, from_unit, to_unit)
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
            from_cur = _normalize_currency(from_unit)
            to_cur = _normalize_currency(to_unit)
            if from_cur and to_cur:
                rates = await _fetch_rates(from_cur)
                if rates is None:
                    return ToolResult(
                        success=False,
                        error=_no_internet_msg(lang),
                    )
                rate = rates.get(to_cur)
                if rate is None:
                    return ToolResult(
                        success=False,
                        error=_unknown_currency_msg(lang, to_cur),
                    )
                converted_val = value * rate
                return ToolResult(
                    success=True,
                    output=_currency_response(lang, value, from_cur, converted_val, to_cur),
                )

            return ToolResult(
                success=False,
                error=_unknown_units_msg(lang, from_unit, to_unit),
            )
    return None


def _try_math(expr: str, lang: str) -> ToolResult | None:
    """Try to evaluate a math expression."""
    # Strip language prefixes
    cleaned = expr
    for prefix in (
        "сколько будет", "посчитай", "скільки буде", "порахуй",
        "what's", "what is", "calculate", "compute", "how much is",
    ):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break

    # Replace spoken operators
    cleaned = cleaned.replace("×", "*").replace("÷", "/")
    cleaned = cleaned.replace("плюс", "+").replace("минус", "-")
    cleaned = cleaned.replace("умножить на", "*").replace("разделить на", "/")
    cleaned = cleaned.replace("помножити на", "*").replace("поділити на", "/")

    try:
        result = safe_math_eval(cleaned)
        return ToolResult(
            success=True,
            output=_math_response(lang, expr, result),
        )
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as exc:
        log.debug("math eval failed for %r: %s", cleaned, exc)
        return None


# ---------------------------------------------------------------------------
# Response formatters — language-aware
# ---------------------------------------------------------------------------

def _pct_response(lang: str, pct: float, base: float, result: float) -> str:
    p = _fmt(pct)
    b = _fmt(base)
    r = _fmt(result)
    if lang == "ru":
        return f"{p}% от {b} — это {r}, сэр."
    if lang == "uk":
        return f"{p}% від {b} — це {r}, сер."
    return f"{p}% of {b} is {r}, sir."


def _math_response(lang: str, expr: str, result: float) -> str:
    r = _fmt(result)
    if lang == "ru":
        return f"{r}, сэр."
    if lang == "uk":
        return f"{r}, сер."
    return f"{r}, sir."


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


def _currency_response(
    lang: str, value: float, from_cur: str, result: float, to_cur: str,
) -> str:
    v = _fmt(value)
    r = _fmt(result)
    if lang == "ru":
        return f"{v} {from_cur} = {r} {to_cur}, сэр."
    if lang == "uk":
        return f"{v} {from_cur} = {r} {to_cur}, сер."
    return f"{v} {from_cur} = {r} {to_cur}, sir."


def _err_msg(lang: str, expr: str) -> str:
    if lang == "ru":
        return f"Не могу вычислить: {expr}"
    if lang == "uk":
        return f"Не можу обчислити: {expr}"
    return f"Could not evaluate: {expr}"


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
