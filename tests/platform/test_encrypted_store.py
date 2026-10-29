"""Tests for jarvis.platform.encrypted_store — EncryptedJsonStore."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.platform.encrypted_store import (
    MAGIC_HEADER,
    EncryptedJsonStore,
    _reset_warning_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_warning():
    """Re-arm the one-per-process warning before every test."""
    _reset_warning_state()
    yield
    _reset_warning_state()


def _fake_protect(data: bytes) -> bytes:
    """Reversible fake for DPAPI _protect — just XOR with 0x42."""
    return bytes(b ^ 0x42 for b in data)


def _fake_unprotect(blob: bytes) -> bytes:
    """Reversible fake for DPAPI _unprotect — XOR with 0x42."""
    return bytes(b ^ 0x42 for b in blob)


@pytest.fixture()
def dpapi_mock():
    """Patch DPAPI to use our fake XOR implementation."""
    with (
        patch("jarvis.platform.encrypted_store.EncryptedJsonStore._encrypt",
               staticmethod(lambda b: _fake_protect(b))),
        patch("jarvis.platform.encrypted_store.EncryptedJsonStore._decrypt",
               staticmethod(lambda b: _fake_unprotect(b))),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests: load/save round-trip
# ---------------------------------------------------------------------------


class TestEncryptedJsonStore:
    """Core read/write/migration tests."""

    def test_save_and_load_dict(self, tmp_path: Path, dpapi_mock):
        """Round-trip: save a dict, reload it, get the same data."""
        path = tmp_path / "test.json"
        store = EncryptedJsonStore(path)
        data = {"name": "Serhii", "city": "Zielona Góra", "items": [1, 2, 3]}
        store.save(data)

        assert path.exists()
        # File must start with magic header (encrypted)
        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER

        loaded = store.load()
        assert loaded == data

    def test_save_and_load_list(self, tmp_path: Path, dpapi_mock):
        """Round-trip with a list as top-level value."""
        path = tmp_path / "bookmarks.json"
        store = EncryptedJsonStore(path)
        data = [{"url": "https://example.com", "name": "Example"}]
        store.save(data)
        loaded = store.load(default=[])
        assert loaded == data

    def test_load_missing_file_returns_default(self, tmp_path: Path):
        """Missing file → return default without error."""
        store = EncryptedJsonStore(tmp_path / "does_not_exist.json")
        assert store.load(default={"empty": True}) == {"empty": True}
        assert store.load(default=[]) == []
        assert store.load() == {}

    def test_load_legacy_plaintext_dict(self, tmp_path: Path, dpapi_mock):
        """Legacy plaintext JSON dict is read correctly."""
        path = tmp_path / "legacy.json"
        data = {"name": "Serhii", "lang": "uk"}
        path.write_text(json.dumps(data), encoding="utf-8")

        store = EncryptedJsonStore(path)
        loaded = store.load()
        assert loaded == data

    def test_load_legacy_plaintext_list(self, tmp_path: Path, dpapi_mock):
        """Legacy plaintext JSON list is read correctly."""
        path = tmp_path / "legacy.json"
        data = [{"url": "https://example.com"}]
        path.write_text(json.dumps(data), encoding="utf-8")

        store = EncryptedJsonStore(path)
        loaded = store.load(default=[])
        assert loaded == data

    def test_legacy_plaintext_auto_encrypted_on_save(
        self, tmp_path: Path, dpapi_mock,
    ):
        """Legacy plaintext is auto-encrypted when save() is called."""
        path = tmp_path / "migrate.json"
        data = {"key": "value"}
        path.write_text(json.dumps(data), encoding="utf-8")

        store = EncryptedJsonStore(path)
        loaded = store.load()
        assert loaded == data

        # Re-save → file should now be encrypted
        store.save(loaded)
        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER

        # Verify round-trip
        loaded2 = store.load()
        assert loaded2 == data

    def test_corrupt_plaintext_returns_default(self, tmp_path: Path):
        """Corrupt plaintext JSON returns default with no crash."""
        path = tmp_path / "corrupt.json"
        path.write_text("{bad json", encoding="utf-8")

        store = EncryptedJsonStore(path)
        result = store.load(default={"fallback": True})
        assert result == {"fallback": True}

    def test_corrupt_encrypted_returns_default(self, tmp_path: Path):
        """Corrupt encrypted file returns default with no crash."""
        path = tmp_path / "corrupt_enc.json"
        path.write_bytes(MAGIC_HEADER + b"not-valid-base64!!!")

        store = EncryptedJsonStore(path)
        result = store.load(default=[])
        assert result == []

    def test_empty_file_returns_default(self, tmp_path: Path):
        """Zero-byte file returns default."""
        path = tmp_path / "empty.json"
        path.write_bytes(b"")

        store = EncryptedJsonStore(path)
        assert store.load(default={"x": 1}) == {"x": 1}

    def test_unknown_format_returns_default(self, tmp_path: Path):
        """Binary gibberish (not JSON, not JRVS_ENC) returns default."""
        path = tmp_path / "binary.json"
        path.write_bytes(b"\x00\x01\x02\x03RANDOM")

        store = EncryptedJsonStore(path)
        assert store.load(default=[]) == []

    def test_creates_parent_directories(self, tmp_path: Path, dpapi_mock):
        """save() creates parent directories as needed."""
        path = tmp_path / "a" / "b" / "c" / "data.json"
        store = EncryptedJsonStore(path)
        store.save({"deep": True})
        assert path.exists()
        assert store.load() == {"deep": True}


class TestNonWindowsFallback:
    """Verify behaviour when DPAPI is unavailable."""

    def test_non_windows_stores_plaintext(self, tmp_path: Path):
        """Off-Windows: file is written as encrypted-looking (magic header)
        but the payload is the plaintext itself (passthrough)."""
        path = tmp_path / "nw.json"
        store = EncryptedJsonStore(path)

        with (
            patch("jarvis.platform.secrets.dpapi_available", return_value=False),
        ):
            store.save({"test": True})
            loaded = store.load()
            assert loaded == {"test": True}

    def test_unicode_round_trip(self, tmp_path: Path, dpapi_mock):
        """Non-ASCII characters survive encryption round-trip."""
        path = tmp_path / "unicode.json"
        store = EncryptedJsonStore(path)
        data = {"город": "Зелёна Гура", "мова": "українська"}
        store.save(data)
        assert store.load() == data
