"""Tests for jarvis.platform.encrypted_db — column-level encryption."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.platform.encrypted_db import (
    decrypt_value,
    encrypt_value,
    needs_migration,
)
from jarvis.platform.secrets import SCHEME_PREFIX

# ---------------------------------------------------------------------------
# Helpers: fake DPAPI for non-Windows test runners
# ---------------------------------------------------------------------------


def _fake_protect(data: bytes) -> bytes:
    """Reversible XOR-based fake for DPAPI."""
    return bytes(b ^ 0x55 for b in data)


def _fake_unprotect(blob: bytes) -> bytes:
    return bytes(b ^ 0x55 for b in blob)


@pytest.fixture()
def dpapi_mock():
    """Make DPAPI calls succeed with a fake XOR cipher."""
    with (
        patch("jarvis.platform.secrets.dpapi_available", return_value=True),
        patch("jarvis.platform.secrets._protect", side_effect=_fake_protect),
        patch("jarvis.platform.secrets._unprotect", side_effect=_fake_unprotect),
    ):
        yield


@pytest.fixture()
def no_dpapi():
    """Simulate non-Windows: DPAPI unavailable."""
    with patch("jarvis.platform.secrets.dpapi_available", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    """encrypt_value / decrypt_value round-trip."""

    def test_round_trip(self, dpapi_mock):
        """Encrypt then decrypt gives back original plaintext."""
        original = "User's name is Serhii"
        encrypted = encrypt_value(original)
        assert encrypted is not None
        assert encrypted != original
        assert encrypted.startswith(SCHEME_PREFIX)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_none_passthrough(self, dpapi_mock):
        assert encrypt_value(None) is None
        assert decrypt_value(None) is None

    def test_empty_passthrough(self, dpapi_mock):
        assert encrypt_value("") == ""
        assert decrypt_value("") == ""

    def test_idempotent_encrypt(self, dpapi_mock):
        """Encrypting an already-encrypted value returns it unchanged."""
        original = "secret text"
        encrypted = encrypt_value(original)
        double_encrypted = encrypt_value(encrypted)
        assert double_encrypted == encrypted

    def test_decrypt_plaintext_passthrough(self, dpapi_mock):
        """Decrypting a plaintext value returns it unchanged (legacy compat)."""
        plain = "legacy plaintext value"
        assert decrypt_value(plain) == plain

    def test_unicode_round_trip(self, dpapi_mock):
        """Non-ASCII text survives encryption."""
        original = "Зелёна Гура — гарне місто"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original


class TestNonWindows:
    """Behaviour when DPAPI is unavailable."""

    def test_encrypt_passthrough(self, no_dpapi):
        """Off-Windows: encrypt_value returns plaintext unchanged."""
        result = encrypt_value("my secret")
        assert result == "my secret"

    def test_decrypt_passthrough(self, no_dpapi):
        """Off-Windows: decrypt_value on plaintext returns it unchanged."""
        result = decrypt_value("my secret")
        assert result == "my secret"


class TestNeedsMigration:
    """needs_migration() identifies legacy plaintext rows."""

    def test_plaintext_needs_migration(self):
        assert needs_migration("User's name is Serhii") is True

    def test_encrypted_does_not_need_migration(self, dpapi_mock):
        encrypted = encrypt_value("secret")
        assert needs_migration(encrypted) is False

    def test_none_does_not_need_migration(self):
        assert needs_migration(None) is False

    def test_empty_does_not_need_migration(self):
        assert needs_migration("") is False
