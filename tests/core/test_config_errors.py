"""Roadmap T1.1: an invalid config.json must give a clear, actionable message
instead of a stack trace. These tests exercise format_config_error() against
real load_config() failures on temporary files (the user's config is never
touched)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core.config import (
    CONFIG_LOAD_ERRORS,
    JarvisConfig,
    _format_validation_item,
    format_config_error,
    load_config,
)


def _load_error(path: Path) -> BaseException:
    with pytest.raises(CONFIG_LOAD_ERRORS) as info:
        load_config(path)
    return info.value


def _write_config(path: Path, mutate) -> None:
    data = JarvisConfig().model_dump(mode="json")
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_json_syntax_error_reports_line_and_column(tmp_path: Path):
    # Task D changed the load_config contract: corrupt JSON no longer crashes
    # startup (it is quarantined + recovered), so we no longer drive this
    # through load_config. format_config_error() itself must still turn a
    # json.JSONDecodeError into a clear line/column message with no traceback
    # — that is what callers using _read_raw() directly (e.g. diagnostics)
    # rely on.
    p = tmp_path / "config.json"
    bad = '{\n  "schema_version": 22,\n  "llm": {,\n}\n'
    try:
        json.loads(bad)
    except json.JSONDecodeError as exc:
        err = exc

    msg = format_config_error(err, p)

    assert "Jarvis cannot start" in msg
    assert str(p) in msg
    assert "JSON syntax error at line 3" in msg
    assert "Traceback" not in msg


def test_out_of_range_value_names_the_field(tmp_path: Path):
    p = tmp_path / "config.json"
    _write_config(p, lambda d: d["llm"].update(temperature=5))

    msg = format_config_error(_load_error(p), p)

    assert "- llm.temperature:" in msg
    assert "got 5" in msg


def test_unknown_key_is_reported_as_typo(tmp_path: Path):
    p = tmp_path / "config.json"
    _write_config(p, lambda d: d["llm"].update(modle="qwen3:8b"))

    msg = format_config_error(_load_error(p), p)

    assert "- llm.modle: unknown key" in msg


def test_sensitive_field_values_are_not_echoed():
    item = {
        "loc": ("research", "brave_api_key"),
        "msg": "Input should be a valid string",
        "type": "string_type",
        "input": "VISIBLE-VALUE",
    }

    line = _format_validation_item(item)

    assert "research.brave_api_key" in line
    assert "VISIBLE-VALUE" not in line
