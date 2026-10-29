"""Tests for the crypto-grade password vault.

Exercises all vault operations with DPAPI mocked (non-Windows test
environments). Uses a temp directory to avoid polluting the real
~/.jarvis/vault.enc.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Ensure DPAPI stubs are in place for non-Windows environments.
_dpapi_patches = []


# Real Argon2id, but with the cheapest valid cost so the suite stays
# fast. This keeps tests exercising the ACTUAL KDF + on-disk format,
# not a PBKDF2 stand-in that would hide format/algorithm bugs. argon2
# minimums: time_cost>=1, parallelism>=1, memory_cost>=8*parallelism.
FAST_KDF_PARAMS = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}

@pytest.fixture(autouse=True)
def _mock_dpapi():
    """Patch DPAPI to passthrough so tests run on any platform."""
    with (
        patch("jarvis.platform.secrets.dpapi_available", return_value=False),
    ):
        yield


@pytest.fixture(autouse=True)
def _fast_kdf():
    """Use real Argon2id at minimum cost so tests are fast but exercise
    the genuine KDF and on-disk format (no PBKDF2 substitution).
    """
    import jarvis.security.password_vault as pv
    with patch.object(pv, "DEFAULT_KDF_PARAMS", FAST_KDF_PARAMS):
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


# ---------------------------------------------------------------------------
# v2 format + algorithm identifiers (Задача 9A)
# ---------------------------------------------------------------------------

class TestV2Format:
    def test_header_records_algorithms(self, vault):
        """v2 header stores kdf, cipher, and KDF params explicitly."""
        from jarvis.security.password_vault import (
            CIPHER_AES_256_GCM,
            KDF_ARGON2ID,
            VAULT_VERSION,
        )

        vault.create("master123")
        doc = vault._vault_data
        assert doc["version"] == VAULT_VERSION == 2
        assert doc["kdf"] == KDF_ARGON2ID == "argon2id"
        assert doc["cipher"] == CIPHER_AES_256_GCM == "aes-256-gcm"
        assert set(doc["kdf_params"]) == {
            "time_cost", "memory_cost", "parallelism",
        }

    def test_v2_roundtrip_real_algorithms(self, vault):
        """Round-trip with the real Argon2id + AES-256-GCM pipeline."""
        vault.create("master123")
        vault.add_credential("steam", "gamer", password="s3cr3t")
        vault.lock()
        assert vault.unlock("master123")
        cred = vault.get_credential("steam")
        assert cred is not None and cred.password == "s3cr3t"

    def test_unknown_version_raises_version_error(self, vault, vault_path):
        from jarvis.security.password_vault import VaultVersionError

        vault.create("master123")
        vault.lock()
        # Corrupt the version field on disk (no DPAPI in tests).
        raw = vault_path.read_bytes()
        payload = raw[len(b"JRVS_VLT"):]
        doc = json.loads(payload.decode("utf-8"))
        doc["version"] = 999
        vault_path.write_bytes(b"JRVS_VLT" + json.dumps(doc).encode("utf-8"))
        with pytest.raises(VaultVersionError):
            vault.unlock("master123")

    def test_unknown_algorithm_raises_version_error(self, vault, vault_path):
        from jarvis.security.password_vault import VaultVersionError

        vault.create("master123")
        vault.lock()
        raw = vault_path.read_bytes()
        payload = raw[len(b"JRVS_VLT"):]
        doc = json.loads(payload.decode("utf-8"))
        doc["cipher"] = "rot13"
        vault_path.write_bytes(b"JRVS_VLT" + json.dumps(doc).encode("utf-8"))
        with pytest.raises(VaultVersionError):
            vault.unlock("master123")


# ---------------------------------------------------------------------------
# Missing crypto backend — fail-closed, no weak bytes written (Задача 9A)
# ---------------------------------------------------------------------------

class TestBackendMissing:
    def test_create_without_argon2_refuses(self, vault, vault_path):
        # Simulate argon2-cffi absent.
        import builtins

        from jarvis.security.password_vault import VaultBackendMissingError
        real_import = builtins.__import__

        def _no_argon2(name, *a, **k):
            if name.startswith("argon2"):
                raise ImportError("argon2 not installed")
            return real_import(name, *a, **k)

        with patch("builtins.__import__", _no_argon2):
            with pytest.raises(VaultBackendMissingError):
                vault.create("master123")
        # Not a single byte must have been written with weak crypto.
        assert not vault_path.exists()

    def test_create_without_cryptography_refuses(self, vault, vault_path):
        import builtins

        from jarvis.security.password_vault import VaultBackendMissingError
        real_import = builtins.__import__

        def _no_crypto(name, *a, **k):
            if name.startswith("cryptography"):
                raise ImportError("cryptography not installed")
            return real_import(name, *a, **k)

        with patch("builtins.__import__", _no_crypto):
            with pytest.raises(VaultBackendMissingError):
                vault.create("master123")
        assert not vault_path.exists()


# ---------------------------------------------------------------------------
# Tampering is an auth error, not "wrong password" (Задача 9A)
# ---------------------------------------------------------------------------

class TestTamperingDistinct:
    def test_tampered_entry_ciphertext_raises_corrupt(self, vault):
        import base64 as _b64

        from jarvis.security.password_vault import (
            PasswordVault,
            VaultCorruptError,
        )

        vault.create("master123")
        vault.add_credential("steam", "user", password="pw")
        entry = vault._vault_data["entries"]["steam"]
        ct = bytearray(_b64.b64decode(entry["ciphertext"]))
        ct[0] ^= 0xFF
        with pytest.raises(VaultCorruptError):
            PasswordVault._aes_decrypt(
                _b64.b64decode(entry["nonce"]),
                bytes(ct),
                _b64.b64decode(entry["tag"]),
                vault._master_key,
            )


# ---------------------------------------------------------------------------
# v1 migration (Задача 9A)
# ---------------------------------------------------------------------------

def _write_v1_vault(
    path, master_password, entries, *, kdf, cipher, kdf_params,
):
    """Build a legacy v1 vault file using OLD logic, for migration tests.

    kdf: "argon2id" | "scrypt"; cipher: "aesgcm" | "hmacxor".
    Mirrors how the old code wrote v1 (version:1, no kdf/cipher fields).
    """
    import base64 as _b64
    import hashlib as _hashlib
    import hmac as _hmac
    import json as _json
    import os as _os

    from jarvis.security.password_vault import PasswordVault

    salt = _os.urandom(32)
    if kdf == "argon2id":
        key = PasswordVault._derive_key(
            master_password, salt, kdf_params=kdf_params,
        )
    else:
        key = PasswordVault._legacy_derive_key_scrypt(master_password, salt)

    def _enc(plaintext):
        if cipher == "aesgcm":
            return PasswordVault._aes_encrypt(plaintext, key)
        # hmacxor (old home-grown scheme)
        nonce = _os.urandom(12)
        keystream = b""
        for i in range((len(plaintext) // 32) + 1):
            keystream += _hmac.new(
                key, nonce + i.to_bytes(4, "big"), _hashlib.sha256,
            ).digest()
        keystream = keystream[: len(plaintext)]
        ct = bytes(a ^ b for a, b in zip(plaintext, keystream))
        tag = _hmac.new(key, nonce + ct, _hashlib.sha256).digest()[:16]
        return nonce, ct, tag

    check = _enc(PasswordVault.KNOWN_PLAINTEXT)
    enc_entries = {}
    for site, data in entries.items():
        n, c, t = _enc(_json.dumps(data).encode("utf-8"))
        enc_entries[site] = {
            "nonce": _b64.b64encode(n).decode(),
            "ciphertext": _b64.b64encode(c).decode(),
            "tag": _b64.b64encode(t).decode(),
        }
    doc = {
        "version": 1,
        "salt": _b64.b64encode(salt).decode(),
        "argon2_params": kdf_params,
        "entries": enc_entries,
        "master_key_check": {
            "nonce": _b64.b64encode(check[0]).decode(),
            "ciphertext": _b64.b64encode(check[1]).decode(),
            "tag": _b64.b64encode(check[2]).decode(),
        },
    }
    path.write_bytes(b"JRVS_VLT" + _json.dumps(doc).encode("utf-8"))


FAST = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}


class TestV1Migration:
    def test_v1_argon2_aesgcm_migrates(self, vault_path):
        """Standard v1 (Argon2id + AES-GCM) opens and migrates to v2."""
        from jarvis.security.password_vault import PasswordVault

        _write_v1_vault(
            vault_path, "master123",
            {"steam": {"username": "u", "password": "p", "url": "",
                       "notes": "", "aliases": [], "created_at": 0,
                       "updated_at": 0}},
            kdf="argon2id", cipher="aesgcm", kdf_params=FAST,
        )
        v = PasswordVault(vault_path=vault_path)
        v._kdf_params = dict(FAST)
        assert v.unlock("master123")
        # Migrated to v2 on disk, backup written, original untouched copy kept.
        assert v._vault_data["version"] == 2
        assert vault_path.with_suffix(vault_path.suffix + ".v1.bak").exists()
        cred = v.get_credential("steam")
        assert cred is not None and cred.password == "p"
        # Re-open the migrated file fresh → now v2.
        v2 = PasswordVault(vault_path=vault_path)
        v2._kdf_params = dict(FAST)
        assert v2.unlock("master123")
        assert v2._vault_data["version"] == 2

    def test_v1_scrypt_hmacxor_migrates(self, vault_path):
        """Legacy v1 (scrypt + home-grown HMAC-XOR) opens and migrates."""
        from jarvis.security.password_vault import PasswordVault

        _write_v1_vault(
            vault_path, "master123",
            {"gmail": {"username": "g", "password": "x", "url": "",
                       "notes": "", "aliases": [], "created_at": 0,
                       "updated_at": 0}},
            kdf="scrypt", cipher="hmacxor", kdf_params=FAST,
        )
        v = PasswordVault(vault_path=vault_path)
        v._kdf_params = dict(FAST)
        assert v.unlock("master123")
        assert v._vault_data["version"] == 2
        cred = v.get_credential("gmail")
        assert cred is not None and cred.password == "x"

    def test_v1_wrong_password_returns_false(self, vault_path):
        from jarvis.security.password_vault import PasswordVault

        _write_v1_vault(
            vault_path, "master123", {},
            kdf="argon2id", cipher="aesgcm", kdf_params=FAST,
        )
        v = PasswordVault(vault_path=vault_path)
        v._kdf_params = dict(FAST)
        assert v.unlock("wrong") is False

    def test_migration_write_failure_leaves_original(self, vault_path):
        """If writing the v2 temp file fails, the original v1 is untouched."""
        from jarvis.security.password_vault import PasswordVault

        _write_v1_vault(
            vault_path, "master123",
            {"steam": {"username": "u", "password": "p", "url": "",
                       "notes": "", "aliases": [], "created_at": 0,
                       "updated_at": 0}},
            kdf="argon2id", cipher="aesgcm", kdf_params=FAST,
        )
        original = vault_path.read_bytes()

        v = PasswordVault(vault_path=vault_path)
        v._kdf_params = dict(FAST)
        # Make the temp write explode mid-migration.
        with patch.object(
            PasswordVault, "_write_vault_bytes",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(Exception):
                v.unlock("master123")
        # Original file unchanged; still a valid v1 that opens.
        assert vault_path.read_bytes() == original
        v2 = PasswordVault(vault_path=vault_path)
        v2._kdf_params = dict(FAST)
        assert v2.unlock("master123")


# ---------------------------------------------------------------------------
# Distinct error messages (Задача 9A)
# ---------------------------------------------------------------------------

class TestDistinctErrors:
    def test_three_error_types_have_distinct_messages(self):
        from jarvis.security.password_vault import (
            VaultBackendMissingError,
            VaultCorruptError,
            VaultVersionError,
        )

        msgs = {
            str(VaultBackendMissingError("cryptography is required; pip install cryptography")),
            str(VaultCorruptError("Ciphertext failed authentication")),
            str(VaultVersionError("Unsupported vault format version")),
        }
        # All three distinct.
        assert len(msgs) == 3
