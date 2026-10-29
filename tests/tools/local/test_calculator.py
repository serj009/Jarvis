"""Tests for jarvis.tools.local.calculator — 30 tests covering:
  - safe_math_eval (basic ops, functions, safety)
  - percentage (EN/RU/UK)
  - unit conversion (length, weight, temp, data, time)
  - currency conversion (mock)
  - voice patterns (EN/RU/UK)
  - error paths
"""

from __future__ import annotations

import math
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.tools.local.calculator import (
    CalculatorArgs,
    CalculatorTool,
    _convert_temperature,
    _fmt,
    _normalize_unit,
    convert_units,
    safe_math_eval,
)
from tests._typing import output_text

# ── safe_math_eval tests ──────────────────────────────────────────────


def test_math_addition():
    assert safe_math_eval("15 + 27") == 42.0


def test_math_subtraction():
    assert safe_math_eval("100 - 37") == 63.0


def test_math_multiplication():
    assert safe_math_eval("6 * 7") == 42.0


def test_math_division():
    assert safe_math_eval("100 / 4") == 25.0


def test_math_power():
    assert safe_math_eval("2 ** 10") == 1024.0


def test_math_parentheses():
    assert safe_math_eval("(2 + 3) * 4") == 20.0


def test_math_modulo():
    assert safe_math_eval("17 % 5") == 2.0


def test_math_sqrt():
    assert safe_math_eval("sqrt(144)") == 12.0


def test_math_sin():
    result = safe_math_eval("sin(0)")
    assert abs(result) < 1e-10


def test_math_pi():
    result = safe_math_eval("pi")
    assert abs(result - math.pi) < 1e-10


def test_math_complex_expression():
    result = safe_math_eval("sqrt(9) + 2 ** 3")
    assert result == 11.0


def test_math_caret_as_power():
    """^ should be normalized to **."""
    assert safe_math_eval("2^10") == 1024.0


def test_math_rejects_import():
    with pytest.raises((ValueError, SyntaxError)):
        safe_math_eval("__import__('os')")


def test_math_rejects_attribute_access():
    with pytest.raises((ValueError, SyntaxError)):
        safe_math_eval("().__class__")


def test_math_division_by_zero():
    with pytest.raises(ZeroDivisionError):
        safe_math_eval("1 / 0")


# ── percentage tests ──────────────────────────────────────────────────


async def test_percent_en():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="15% of 3500"))
    assert result.success
    output = output_text(result)
    assert "525" in output
    assert "sir" in output.lower()


async def test_percent_ru():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="15% от 3500"))
    assert result.success
    output = output_text(result)
    assert "525" in output
    assert "сэр" in output.lower()


async def test_percent_uk():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="15% від 3500"))
    assert result.success
    output = output_text(result)
    assert "525" in output
    assert "сер" in output.lower()


async def test_percent_decimal():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="7.5% of 200"))
    assert result.success
    assert "15" in output_text(result)


# ── unit conversion tests ────────────────────────────────────────────


def test_convert_km_to_miles():
    result = convert_units(10.0, "km", "miles")
    assert result is not None
    assert abs(result - 6.21371) < 0.001


def test_convert_miles_to_km():
    result = convert_units(1.0, "miles", "km")
    assert result is not None
    assert abs(result - 1.609344) < 0.001


def test_convert_kg_to_lbs():
    result = convert_units(1.0, "kg", "lbs")
    assert result is not None
    assert abs(result - 2.20462) < 0.001


def test_convert_mb_to_gb():
    result = convert_units(1024.0, "mb", "gb")
    assert result is not None
    assert abs(result - 1.0) < 0.001


def test_convert_hours_to_minutes():
    result = convert_units(2.0, "hours", "minutes")
    assert result is not None
    assert abs(result - 120.0) < 0.001


def test_convert_incompatible_units():
    """kg to km should return None — different dimensions."""
    result = convert_units(1.0, "kg", "km")
    assert result is None


def test_convert_cyrillic_aliases():
    """Russian/Ukrainian unit aliases should resolve."""
    assert _normalize_unit("км") == "km"
    assert _normalize_unit("мб") == "mb"
    assert _normalize_unit("кг") == "kg"


# ── temperature conversion tests ─────────────────────────────────────


def test_temp_c_to_f():
    result = _convert_temperature(100.0, "C", "F")
    assert result is not None
    assert abs(result - 212.0) < 0.01


def test_temp_f_to_c():
    result = _convert_temperature(32.0, "F", "C")
    assert result is not None
    assert abs(result - 0.0) < 0.01


def test_temp_c_to_k():
    result = _convert_temperature(0.0, "C", "K")
    assert result is not None
    assert abs(result - 273.15) < 0.01


def test_temp_same_unit():
    result = _convert_temperature(50.0, "F", "F")
    assert result is not None
    assert abs(result - 50.0) < 0.01


def test_temp_not_temperature():
    """Non-temperature units should return None."""
    result = _convert_temperature(10.0, "km", "miles")
    assert result is None


# ── full tool integration (conversion via execute) ────────────────────


async def test_tool_convert_en():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="convert 10 km to miles"))
    assert result.success
    output = output_text(result)
    assert "6.21" in output
    assert "sir" in output.lower()


async def test_tool_convert_ru():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="переведи 100 C в F"))
    assert result.success
    output = output_text(result)
    assert "212" in output
    assert "сэр" in output.lower()


async def test_tool_convert_uk():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="переведи 1 kg в lbs"))
    # "переведи" without і/ї/є/ґ → RU detection, but output still works
    assert result.success
    assert "2.2" in output_text(result)


# ── currency conversion (mocked) ─────────────────────────────────────


async def test_currency_conversion_en():
    tool = CalculatorTool()
    mock_rates = {"EUR": 0.85, "GBP": 0.73}
    with patch(
        "jarvis.tools.local.calculator._fetch_rates",
        new_callable=AsyncMock,
        return_value=mock_rates,
    ):
        result = await tool.execute(CalculatorArgs(expression="convert 100 USD to EUR"))
    assert result.success
    output = output_text(result)
    assert "85" in output
    assert "sir" in output.lower()


async def test_currency_no_internet():
    tool = CalculatorTool()
    with patch(
        "jarvis.tools.local.calculator._fetch_rates",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await tool.execute(CalculatorArgs(expression="convert 100 USD to EUR"))
    assert not result.success
    assert "internet" in (result.error or "").lower()


# ── math via execute ──────────────────────────────────────────────────


async def test_tool_math_en():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="15 + 27"))
    assert result.success
    assert "42" in output_text(result)


async def test_tool_math_ru():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="сколько будет 6 * 7"))
    assert result.success
    output = output_text(result)
    assert "42" in output
    assert "сэр" in output.lower()


async def test_tool_math_uk():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="скільки буде 2 ** 8"))
    assert result.success
    output = output_text(result)
    assert "256" in output
    assert "сер" in output.lower()


# ── voice patterns ────────────────────────────────────────────────────


def _match_pattern(text: str) -> dict | None:
    """Try to match text against all CalculatorTool voice patterns."""
    for vp in CalculatorTool.voice_patterns:
        m = re.match(vp.regex, text, re.IGNORECASE)
        if m:
            return vp.args(m)
    return None


def test_voice_pattern_en_math():
    result = _match_pattern("15 + 27")
    assert result is not None
    assert result["expression"] == "15 + 27"


def test_voice_pattern_en_calculate():
    result = _match_pattern("calculate sqrt(144)")
    assert result is not None
    assert "sqrt(144)" in result["expression"]


def test_voice_pattern_en_percent():
    result = _match_pattern("15 percent of 3500")
    assert result is not None
    assert "15" in result["expression"]
    assert "3500" in result["expression"]


def test_voice_pattern_en_convert():
    result = _match_pattern("convert 10 km to miles")
    assert result is not None
    assert "10" in result["expression"]


def test_voice_pattern_ru_skolko():
    result = _match_pattern("сколько будет 5 * 9")
    assert result is not None
    assert "5 * 9" in result["expression"]


def test_voice_pattern_ru_percent():
    result = _match_pattern("15% от 3500")
    assert result is not None


def test_voice_pattern_ru_convert():
    result = _match_pattern("переведи 10 км в мили")
    assert result is not None


def test_voice_pattern_uk_skilky():
    result = _match_pattern("скільки буде 100 + 200")
    assert result is not None
    assert "100 + 200" in result["expression"]


def test_voice_pattern_uk_percent():
    result = _match_pattern("15% від 3500")
    assert result is not None


def test_voice_pattern_uk_convert():
    result = _match_pattern("конвертуй 5 кг у lbs")
    assert result is not None


# ── error paths ───────────────────────────────────────────────────────


async def test_invalid_expression():
    tool = CalculatorTool()
    result = await tool.execute(CalculatorArgs(expression="hello world"))
    assert not result.success


# ── formatting ────────────────────────────────────────────────────────


def test_fmt_integer():
    assert _fmt(1000.0) == "1 000"


def test_fmt_float():
    result = _fmt(3.14159)
    assert "3.14" in result


# ── metadata ──────────────────────────────────────────────────────────


def test_tool_metadata():
    tool = CalculatorTool()
    assert tool.name == "calculator"
    assert "math" in tool.description.lower()
    assert tool.requires_confirmation is False
