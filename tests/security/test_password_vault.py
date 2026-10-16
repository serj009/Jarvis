"""Tests for the crypto-grade password vault.

Exercises all vault operations with DPAPI mocked (non-Windows test
environments). Uses a temp directory to avoid polluting the real
~/.jarvis/vault.enc.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure DPAPI stubs are in place for non-Windows environments.
_dpapi_patches = []


def _fast_derive_key(password: Any, salt: bytes) -> bytes:
    """Test-only KDF: PBKDF2 with minimal iterations.

    The production ``_derive_key`` uses Argon2id or ``hashlib.scrypt``
    with n=2**17 (128 MB memory, several seconds per call).  That is
    correct for security but makes the test suite unusably slow or can
    even fail on resource-constrained CI runners.

    This substitute is deterministic (same password + salt → same key)
    and fast enough for hundreds of vault round-trips per second.
    """
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations=1, dklen=32)

@pytest.fixture(autouse=True)
def _mock_dpapi():
    """Patch DPAPI to passthrough so tests run on any platform."""
    with (
        patch("jarvis.platform.secrets.dpapi_available", return_value=False),
    ):
        yield


@pytest.fixture(autouse=True)
def _fast_kdf():
    """Replace the heavy Argon2/scrypt KDF with a fast PBKDF2 for tests.

    Without this, each ``_derive_key`` call allocates 128 MB and takes
    several seconds, making the vault tests impractically slow.
    """
    with patch("jarvis.security.password_vault.PasswordVault._derive_key", staticmethod(_fast_derive_key)):
        yield


@pytest.fixture()
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "vault.enc"


@pytest.fixture()
def vault(vault_path: Path):
    from jarvis.security.password_vault import PasswordVault

    return PasswordVault(vault_path=vault_path)


# ---------------------------------------------------------------------------
# Vault lifecycle
# ---------------------------------------------------------------------------

class TestVaultLifecycle:
    def test_create_and_unlock(self, vault, vault_path):
        """Create vault, lock it, then unlock with correct password."""
        vault.create("master123")
        assert vault.is_unlocked
        assert vault_path.exists()

        vault.lock()
        assert not vault.is_unlocked

        ok = vault.unlock("master123")
        assert ok
        assert vault.is_unlocked

    def test_wrong_master_password_fails(self, vault):
        """Unlocking with wrong password returns False."""
        vault.create("correct_password")
        vault.lock()

        ok = vault.unlock("wrong_password")
        assert not ok
        assert not vault.is_unlocked

    def test_create_duplicate_raises(self, vault):
        """Creating a vault when one already exists raises."""
        from jarvis.security.password_vault import VaultError

        vault.create("master123")
        with pytest.raises(VaultError, match="already exists"):
            vault.create("master456")

    def test_lock_wipes_key(self, vault):
        """Lock wipes the master key from memory."""
        vault.create("master123")
        assert vault._master_key is not None

        vault.lock()
        assert vault._master_key is None
        assert vault._vault_data is None
        assert not vault.is_unlocked


# ---------------------------------------------------------------------------
# Credential CRUD
# ---------------------------------------------------------------------------

class TestCredentialCRUD:
    def test_add_and_get_credential(self, vault):
        """Add a credential, then retrieve it."""
        vault.create("master123")

        vault.add_credential(
            site="steam",
            username="gamer42",
            password="s3cr3tP@ss",
            url="https://store.steampowered.com/login/",
            notes="Main gaming account",
            aliases=["стім", "стим"],
        )

        cred = vault.get_credential("steam")
        assert cred is not None
        assert cred.site == "steam"
        assert cred.username == "gamer42"
        assert cred.password == "s3cr3tP@ss"
        assert cred.url == "https://store.steampowered.com/login/"
        assert cred.notes == "Main gaming account"
        assert "стім" in cred.aliases

    def test_get_credential_case_insensitive(self, vault):
        """Site names are case-insensitive."""
        vault.create("master123")
        vault.add_credential("Steam", "user", password="mypass")

        cred = vault.get_credential("STEAM")
        assert cred is not None
        assert cred.username == "user"

    def test_get_credential_by_alias(self, vault):
        """Can find credentials by alias."""
        vault.create("master123")
        vault.add_credential(
            "steam", "user", password="mypass", aliases=["стім", "стим"]
        )

        cred = vault.get_credential("стім")
        assert cred is not None
        assert cred.site == "steam"
        assert cred.username == "user"

    def test_get_nonexistent_credential(self, vault):
        """Getting a non-existent credential returns None."""
        vault.create("master123")

        cred = vault.get_credential("nonexistent")
        assert cred is None

    def test_list_sites_no_passwords(self, vault):
        """list_sites returns only site names, never passwords."""
        vault.create("master123")
        vault.add_credential("steam", "user1", password="pass1")
        vault.add_credential("gmail", "user2", password="pass2")
        vault.add_credential("rozetka", "user3", password="pass3")

        sites = vault.list_sites()
        assert set(sites) == {"steam", "gmail", "rozetka"}
        # The sites list is just strings — no VaultEntry objects
        for s in sites:
            assert isinstance(s, str)

    def test_remove_credential(self, vault):
        """Remove a credential."""
        vault.create("master123")
        vault.add_credential("steam", "user", password="mypass")

        removed = vault.remove_credential("steam")
        assert removed

        cred = vault.get_credential("steam")
        assert cred is None

    def test_remove_nonexistent(self, vault):
        """Removing a non-existent credential returns False."""
        vault.create("master123")

        removed = vault.remove_credential("nonexistent")
        assert not removed


# ---------------------------------------------------------------------------
# Encryption / Security
# ---------------------------------------------------------------------------

class TestEncryption:
    def test_vault_file_is_encrypted_no_plaintext(self, vault, vault_path):
        """Vault file must not contain plaintext passwords."""
        vault.create("master123")
        vault.add_credential("steam", "gamer42", password="s3cr3tP@ss")

        raw = vault_path.read_bytes()
        # The raw file should NOT contain the plaintext password
        assert b"s3cr3tP@ss" not in raw
        # Should not contain plaintext username either (it's AES-encrypted)
        # (username is in the AES-encrypted entry JSON, not in plaintext)
        # The magic header should be present
        assert raw.startswith(b"JRVS_VLT")

    def test_aes_encrypt_decrypt_roundtrip(self):
        """AES encryption/decryption round-trip works."""
        from jarvis.security.password_vault import PasswordVault

        key = b"\x00" * 32  # 32-byte key for testing
        plaintext = b"Hello, this is a secret message!"

        nonce, ct, tag = PasswordVault._aes_encrypt(plaintext, key)

        assert len(nonce) == 12
        assert len(tag) == 16
        assert ct != plaintext  # ciphertext differs from plaintext

        decrypted = PasswordVault._aes_decrypt(nonce, ct, tag, key)
        assert decrypted == plaintext

    def test_aes_tampered_tag_fails(self):
        """Tampered authentication tag causes decryption failure."""
        from jarvis.security.password_vault import PasswordVault

        key = b"\x01" * 32
        plaintext = b"Important data"

        nonce, ct, tag = PasswordVault._aes_encrypt(plaintext, key)

        # Tamper with the tag
        bad_tag = bytes((tag[0] ^ 0xFF,)) + tag[1:]
        with pytest.raises(Exception):  # VaultCorruptError or crypto exception
            PasswordVault._aes_decrypt(nonce, ct, bad_tag, key)

    def test_key_derivation_deterministic(self):
        """Same password + salt → same key."""
        from jarvis.security.password_vault import PasswordVault

        salt = b"fixed_salt_for_testing_32bytes!!"
        assert len(salt) == 32

        key1 = PasswordVault._derive_key("my_password", salt)
        key2 = PasswordVault._derive_key("my_password", salt)
        assert key1 == key2
        assert len(key1) == 32

    def test_key_derivation_different_passwords(self):
        """Different passwords → different keys."""
        from jarvis.security.password_vault import PasswordVault

        salt = b"fixed_salt_for_testing_32bytes!!"
        key1 = PasswordVault._derive_key("password_a", salt)
        key2 = PasswordVault._derive_key("password_b", salt)
        assert key1 != key2

    def test_secure_zero(self):
        """secure_zero wipes data in-place."""
        from jarvis.security.password_vault import PasswordVault

        data = bytearray(b"sensitive_data_here!")
        assert any(b != 0 for b in data)

        PasswordVault._secure_zero(data)
        assert all(b == 0 for b in data)

    def test_each_entry_has_unique_nonce(self, vault):
        """Each entry must have a unique AES nonce."""
        vault.create("master123")
        vault.add_credential("site1", "user1", password="pass1")
        vault.add_credential("site2", "user2", password="pass2")

        entries = vault._vault_data["entries"]
        nonce1 = entries["site1"]["nonce"]
        nonce2 = entries["site2"]["nonce"]
        assert nonce1 != nonce2, "Each entry must have a unique nonce"


# ---------------------------------------------------------------------------
# Vault locked errors
# ---------------------------------------------------------------------------

class TestVaultLocked:
    def test_add_credential_when_locked(self, vault):
        """Adding credential when locked raises VaultLockedError."""
        from jarvis.security.password_vault import VaultLockedError

        # vault is not created/unlocked yet
        with pytest.raises(VaultLockedError):
            vault.add_credential("site", "user", password="mypass")

    def test_get_credential_when_locked(self, vault):
        """Getting credential when locked raises VaultLockedError."""
        from jarvis.security.password_vault import VaultLockedError

        with pytest.raises(VaultLockedError):
            vault.get_credential("site")

    def test_list_sites_when_locked(self, vault):
        """Listing sites when locked raises VaultLockedError."""
        from jarvis.security.password_vault import VaultLockedError

        with pytest.raises(VaultLockedError):
            vault.list_sites()


# ---------------------------------------------------------------------------
# Change master password
# ---------------------------------------------------------------------------

class TestChangeMasterPassword:
    def test_change_password_success(self, vault):
        """Change master password, then unlock with new password."""
        vault.create("old_password")
        vault.add_credential("steam", "user", password="mypass")

        ok = vault.change_master_password("old_password", "new_password")
        assert ok

        vault.lock()

        # Old password should fail
        assert not vault.unlock("old_password")

        # New password should work
        assert vault.unlock("new_password")

        # Credential should still be there
        cred = vault.get_credential("steam")
        assert cred is not None
        assert cred.password== "mypass"

    def test_change_password_wrong_old(self, vault):
        """Change with wrong old password fails."""
        vault.create("correct_password")
        vault.lock()

        ok = vault.change_master_password("wrong_old", "new_password")
        assert not ok


# ---------------------------------------------------------------------------
# Non-Windows fallback (no DPAPI)
# ---------------------------------------------------------------------------

class TestNonWindowsFallback:
    def test_vault_works_without_dpapi(self, vault):
        """Full vault lifecycle works without DPAPI (non-Windows)."""
        # _mock_dpapi fixture already patches dpapi_available → False
        vault.create("master123")
        vault.add_credential("gmail", "user@gmail.com", password="gmail_pass!")

        vault.lock()
        assert vault.unlock("master123")

        cred = vault.get_credential("gmail")
        assert cred is not None
        assert cred.username == "user@gmail.com"
        assert cred.password == "gmail_pass!"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_vault_list_sites(self, vault):
        """Empty vault returns empty list."""
        vault.create("master123")
        assert vault.list_sites() == []

    def test_overwrite_existing_credential(self, vault):
        """Adding a credential with same site name overwrites it."""
        vault.create("master123")
        vault.add_credential("steam", "old_user", password="old_pass")
        vault.add_credential("steam", "new_user", password="new_pass")

        cred = vault.get_credential("steam")
        assert cred is not None
        assert cred.username == "new_user"
        assert cred.password== "new_pass"

    def test_unicode_credentials(self, vault):
        """Unicode characters in credentials work correctly."""
        vault.create("мастер_пароль")
        vault.add_credential(
            "rozetka.ua",
            "Сергій",
            password="s3cr3tP@ss",
            aliases=["розетка"],
        )

        cred = vault.get_credential("rozetka.ua")
        assert cred is not None
        assert cred.username == "Сергій"
        assert cred.password== "s3cr3tP@ss"

        # Also find by alias
        cred2 = vault.get_credential("розетка")
        assert cred2 is not None
        assert cred2.site == "rozetka.ua"

    def test_vault_persists_across_instances(self, vault_path):
        """Vault data persists when re-opened from a new instance."""
        from jarvis.security.password_vault import PasswordVault

        v1 = PasswordVault(vault_path=vault_path)
        v1.create("master123")
        v1.add_credential("steam", "user", password="persist_pw")
        v1.lock()

        v2 = PasswordVault(vault_path=vault_path)
        assert v2.unlock("master123")
        cred = v2.get_credential("steam")
        assert cred is not None
        assert cred.password== "persist_pw"
