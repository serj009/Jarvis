"""Tests for jarvis.audio.language_state (Task 7.3: last-language memory).

The autouse _isolate_data_dir fixture (tests/conftest.py) points
JARVIS_DATA_DIR at a throwaway temp dir, so these exercise the real
data_path()/atomic_write_json round-trip without touching the user's data.
"""

from __future__ import annotations

from jarvis.audio.language_state import load_last_language, save_last_language
from jarvis.paths import data_path

_ENABLED = frozenset({"en", "ru", "uk"})


def test_missing_file_returns_default() -> None:
    assert load_last_language(_ENABLED, default="en") == "en"


def test_round_trip_saves_and_loads() -> None:
    save_last_language("uk")
    assert load_last_language(_ENABLED, default="en") == "uk"


def test_save_overwrites_previous() -> None:
    save_last_language("ru")
    save_last_language("uk")
    assert load_last_language(_ENABLED, default="en") == "uk"


def test_language_not_enabled_falls_back_to_default() -> None:
    save_last_language("fr")  # written, but not an enabled language
    assert load_last_language(_ENABLED, default="en") == "en"
    # A narrower enabled set also rejects a previously-valid language.
    save_last_language("uk")
    assert load_last_language(frozenset({"en", "ru"}), default="ru") == "ru"


def test_corrupt_file_falls_back_to_default() -> None:
    path = data_path("state", "last_language.json", create=True)
    path.write_text("{ this is not json", encoding="utf-8")
    assert load_last_language(_ENABLED, default="en") == "en"


def test_missing_key_falls_back_to_default() -> None:
    path = data_path("state", "last_language.json", create=True)
    path.write_text('{"lang": "uk"}', encoding="utf-8")  # wrong key
    assert load_last_language(_ENABLED, default="en") == "en"


def test_non_string_value_falls_back_to_default() -> None:
    path = data_path("state", "last_language.json", create=True)
    path.write_text('{"language": 123}', encoding="utf-8")
    assert load_last_language(_ENABLED, default="en") == "en"


def test_save_failure_is_swallowed(monkeypatch) -> None:
    """A write error must never bubble out of save_last_language."""
    import jarvis.audio.language_state as mod

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "atomic_write_json", _boom)
    # Must not raise.
    save_last_language("ru")
