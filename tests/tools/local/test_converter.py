"""Tests for jarvis.tools.local.converter — 18 tests covering:
  - unit conversions (length, weight, temperature, volume, data, time)
  - yards and ml/fl_oz (converter-specific units)
  - currency conversion (mock)
  - voice patterns (EN/RU/UK)
  - "how many" question form
  - error paths
  - metadata & ast.parse
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jarvis.tools.local.converter import (
    ConverterArgs,
    ConverterTool,
    _fmt,
    _normalize_unit,
    convert_temperature,
    convert_units,
    normalize_currency,
)
from tests._typing import output_text


# ── unit conversion (built-in) ──────────────────────────────────────────


def test_convert_km_to_miles():
    result = convert_units(10.0, "km", "miles")
    assert result is not None
    assert abs(result - 6.21371) < 0.001


def test_convert_miles_to_km():
    result = convert_units(5.0, "miles", "km")
    assert result is not None
    assert abs(result - 8.04672) < 0.001


def test_convert_m_to_yards():
    """Yards conversion — unique to converter, not in calculator."""
    result = convert_units(100.0, "m", "yards")
    assert result is not None
    assert abs(result - 109.361) < 0.01


def test_convert_kg_to_lbs():
    result = convert_units(1.0, "kg", "lbs")
    assert result is not None
    assert abs(result - 2.20462) < 0.001


def test_convert_ml_to_fl_oz():
    """ml↔fl_oz — unique to converter."""
    result = convert_units(100.0, "ml", "fl_oz")
    assert result is not None
    assert abs(result - 3.3814) < 0.01


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


# ── temperature ──────────────────────────────────────────────────────────


def test_temp_f_to_c():
    result = convert_temperature(100.0, "fahrenheit", "celsius")
    assert result is not None
    assert abs(result - 37.7778) < 0.01


def test_temp_c_to_k():
    result = convert_temperature(0.0, "C", "K")
    assert result is not None
    assert abs(result - 273.15) < 0.01


# ── cyrillic aliases ─────────────────────────────────────────────────────


def test_cyrillic_unit_aliases():
    assert _normalize_unit("км") == "km"
    assert _normalize_unit("мб") == "mb"
    assert _normalize_unit("кг") == "kg"
    assert _normalize_unit("ярд") == "yards"
    assert _normalize_unit("мл") == "ml"


# ── currency aliases ─────────────────────────────────────────────────────


def test_currency_aliases():
    assert normalize_currency("dollars") == "USD"
    assert normalize_currency("гривны") == "UAH"
    assert normalize_currency("рубли") == "RUB"
    assert normalize_currency("злотые") == "PLN"
    assert normalize_currency("євро") is None  # not in aliases
    assert normalize_currency("EUR") == "EUR"


# ── full tool execution (EN) ─────────────────────────────────────────────


async def test_tool_how_many_en():
    """EN: 'how many km in 5 miles'."""
    tool = ConverterTool()
    result = await tool.execute(ConverterArgs(query="how many km in 5 miles"))
    assert result.success
    output = output_text(result)
    assert "8.04" in output or "8.05" in output
    assert "sir" in output.lower()


async def test_tool_value_to_en():
    """EN: '100 fahrenheit in celsius'."""
    tool = ConverterTool()
    result = await tool.execute(ConverterArgs(query="100 fahrenheit in celsius"))
    assert result.success
    output = output_text(result)
    assert "37.7" in output or "37.8" in output
    assert "sir" in output.lower()


# ── full tool execution (RU) ─────────────────────────────────────────────


async def test_tool_how_many_ru():
    """RU: 'сколько км в 5 милях'."""
    tool = ConverterTool()
    result = await tool.execute(ConverterArgs(query="сколько км в 5 милях"))
    assert result.success
    output = output_text(result)
    assert "8.04" in output or "8.05" in output
    assert "сэр" in output.lower()


# ── full tool execution (UK) ─────────────────────────────────────────────


async def test_tool_how_many_uk():
    """UK: 'скільки км у 5 милях'."""
    tool = ConverterTool()
    result = await tool.execute(ConverterArgs(query="скільки км у 5 милях"))
    assert result.success
    output = output_text(result)
    assert "8.04" in output or "8.05" in output
    assert "сер" in output.lower()


# ── currency conversion (mocked) ────────────────────────────────────────


async def test_currency_conversion_en():
    tool = ConverterTool()
    mock_rates = {"UAH": 41.5, "EUR": 0.85}
    with patch(
        "jarvis.tools.local.converter.fetch_rates",
        new_callable=AsyncMock,
        return_value=mock_rates,
    ):
        result = await tool.execute(ConverterArgs(query="100 dollars to hryvnias"))
    assert result.success
    output = output_text(result)
    assert "4 150" in output or "4150" in output
    assert "sir" in output.lower()


async def test_currency_no_internet():
    tool = ConverterTool()
    with patch(
        "jarvis.tools.local.converter.fetch_rates",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await tool.execute(ConverterArgs(query="100 dollars to hryvnias"))
    assert not result.success
    assert "internet" in (result.error or "").lower()


# ── voice patterns ───────────────────────────────────────────────────────


def _match_pattern(text: str) -> dict | None:
    """Try to match text against all ConverterTool voice patterns."""
    for vp in ConverterTool.voice_patterns:
        m = re.match(vp.regex, text, re.IGNORECASE)
        if m:
            return vp.args(m)
    return None


def test_voice_pattern_how_many_en():
    result = _match_pattern("how many km in 5 miles")
    assert result is not None
    assert "query" in result


def test_voice_pattern_value_to_en():
    result = _match_pattern("100 dollars to hryvnias")
    assert result is not None
    assert "query" in result


def test_voice_pattern_ru_skolko():
    result = _match_pattern("сколько км в 5 милях")
    assert result is not None
    assert "query" in result


def test_voice_pattern_ru_perevedi():
    result = _match_pattern("переведи 100 долларов в гривны")
    assert result is not None
    assert "query" in result


def test_voice_pattern_uk_skilky():
    result = _match_pattern("скільки км у 5 милях")
    assert result is not None
    assert "query" in result


def test_voice_pattern_uk_konvertuj():
    result = _match_pattern("конвертуй 100 доларів у гривні")
    assert result is not None
    assert "query" in result


# ── error / unknown input ────────────────────────────────────────────────


async def test_unparseable_query():
    tool = ConverterTool()
    result = await tool.execute(ConverterArgs(query="hello world"))
    assert not result.success


# ── metadata ─────────────────────────────────────────────────────────────


def test_tool_metadata():
    tool = ConverterTool()
    assert tool.name == "converter"
    assert "convert" in tool.description.lower()
    assert tool.requires_confirmation is False


# ── ast.parse (file is valid Python, no exec/eval) ───────────────────────


def test_ast_parse():
    """The converter module must parse cleanly under ast.parse."""
    import jarvis.tools.local.converter as _converter_mod

    src = Path(_converter_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # no exec() or eval() calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("exec", "eval"), (
                f"Found {node.func.id}() call at line {node.lineno}"
            )
