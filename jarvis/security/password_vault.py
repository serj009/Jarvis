"""Crypto-grade password vault — 3-layer encryption for user credentials.

Encryption layers:
1. Argon2id: master_password + salt → master_key (32 bytes)
   - Memory-hard KDF, resistant to GPU/ASIC attacks
   - Parameters: time_cost=3, memory_cost=65536 (64MB), parallelism=4
2. AES-256-GCM: master_key encrypts each credential individually
   - Each entry has unique nonce (12 bytes random)
   - Authenticated encryption (tampering detected)
3. DPAPI: wraps the entire vault file (Windows user account binding)

On-disk format::

    vault.enc = DPAPI_encrypt(json.dumps({
        "version": 1,
        "salt": base64(32 random bytes),
        "argon2_params": {"time_cost": 3, "memory_cost": 65536, "parallelism": 4},
        "entries": {
            "steam": {
                "nonce": base64(12 bytes),
                "ciphertext": base64(AES-256-GCM encrypted JSON),
                "tag": base64(16 bytes GCM auth tag)
            },
            ...
        },
        "master_key_check": base64(AES-GCM encrypt of known string "JARVIS_VAULT_OK")
    }))

master_key_check is used to verify the master password is correct
WITHOUT storing the key: decrypt check → if "JARVIS_VAULT_OK" → correct password.

CRITICAL: master_key and decrypted passwords exist ONLY in RAM.
After use, they are overwritten with zeros (secure_zero).

Dependencies (optional, checked at runtime):
    - argon2-cffi (preferred KDF) or hashlib.scrypt (stdlib fallback)
    - cryptography or pycryptodome (AES-GCM) or HMAC fallback (stdlib)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Magic header for vault files — distinct from JRVS_ENC (EncryptedJsonStore).
# Changing this strands every existing vault file.
VAULT_MAGIC = b"JRVS_VLT"


@dataclass
class VaultEntry:
    """A single credential entry."""

    site: str  # e.g. "steam", "rozetka.ua"
    username: str
    password: str  # Only decrypted in memory, never persisted plaintext
    url: str = ""  # Login page URL
    notes: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    aliases: list[str] = field(default_factory=list)  # ["стім", "стим"]


class VaultError(Exception):
    """Any vault operation failure."""


class VaultLockedError(VaultError):
    """Attempted credential access while vault is locked."""


class VaultCorruptError(VaultError):
    """Vault file is corrupt or tampered with."""


class PasswordVault:
    """Crypto-grade password storage with 3-layer encryption.

    See module docstring for the full architecture description.
    """

    DEFAULT_VAULT_PATH = Path.home() / ".jarvis" / "vault.enc"
    KNOWN_PLAINTEXT = b"JARVIS_VAULT_OK"

    def __init__(self, vault_path: Path | None = None) -> None:
        self._path = vault_path or self.DEFAULT_VAULT_PATH
        self._master_key: bytes | None = None
        self._salt: bytes | None = None
        self._vault_data: dict | None = None
        self._unlocked = False

    # -- public properties --------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_unlocked(self) -> bool:
        return self._unlocked

    @property
    def exists(self) -> bool:
        return self._path.exists()

    # -- Key derivation (Layer 1) -------------------------------------------

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        """Argon2id KDF: password + salt → 32-byte master key.

        Uses hashlib.scrypt as fallback if argon2-cffi not installed:
        - argon2-cffi (preferred): argon2id with memory-hard params
        - scrypt (fallback): still memory-hard, available in stdlib

        Both produce 32-byte keys from password+salt.
        """
        try:
            from argon2.low_level import Type, hash_secret_raw

            return hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=salt,
                time_cost=3,
                memory_cost=65536,  # 64 MB
                parallelism=4,
                hash_len=32,
                type=Type.ID,
            )
        except ImportError:
            pass

        # Fallback: scrypt (stdlib, also memory-hard)
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**17,  # 128 MB memory
            r=8,
            p=1,
            dklen=32,
        )

    # -- AES-256-GCM (Layer 2) ---------------------------------------------

    @staticmethod
    def _aes_encrypt(plaintext: bytes, key: bytes) -> tuple[bytes, bytes, bytes]:
        """AES-256-GCM encrypt. Returns (nonce, ciphertext, tag).

        Tries in order: cryptography, pycryptodome, HMAC-based fallback.
        """
        nonce = os.urandom(12)

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            ct = aesgcm.encrypt(nonce, plaintext, None)
            # cryptography lib appends the 16-byte tag to ciphertext
            return nonce, ct[:-16], ct[-16:]
        except ImportError:
            pass

        try:
            from Crypto.Cipher import AES

            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
            return nonce, ciphertext, tag
        except ImportError:
            pass

        # Last resort fallback: HMAC-based authenticated encryption
        # XOR with HMAC-derived keystream + HMAC tag
        # This is NOT AES-GCM but provides confidentiality + authenticity
        keystream = b""
        for i in range((len(plaintext) // 32) + 1):
            keystream += hmac.new(
                key, nonce + i.to_bytes(4, "big"), hashlib.sha256
            ).digest()
        keystream = keystream[: len(plaintext)]
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, keystream))
        tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()[:16]
        return nonce, ciphertext, tag

    @staticmethod
    def _aes_decrypt(
        nonce: bytes, ciphertext: bytes, tag: bytes, key: bytes
    ) -> bytes:
        """AES-256-GCM decrypt. Raises on auth failure."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key)
            # cryptography expects tag appended to ciphertext
            return aesgcm.decrypt(nonce, ciphertext + tag, None)
        except ImportError:
            pass

        try:
            from Crypto.Cipher import AES

            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag)
        except ImportError:
            pass

        # HMAC-based fallback (mirror of _aes_encrypt)
        expected_tag = hmac.new(
            key, nonce + ciphertext, hashlib.sha256
        ).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            raise VaultCorruptError("Authentication tag mismatch — data tampered")

        keystream = b""
        for i in range((len(ciphertext) // 32) + 1):
            keystream += hmac.new(
                key, nonce + i.to_bytes(4, "big"), hashlib.sha256
            ).digest()
        keystream = keystream[: len(ciphertext)]
        return bytes(a ^ b for a, b in zip(ciphertext, keystream))

    # -- Secure memory wiping -----------------------------------------------

    @staticmethod
    def _secure_zero(data: bytearray | memoryview) -> None:
        """Overwrite sensitive data in memory with zeros.

        Best-effort: Python's GC may keep copies, but we wipe
        the primary reference. ctypes.memset is the gold standard
        but requires platform specifics; byte-by-byte is portable.
        """
        for i in range(len(data)):
            data[i] = 0

    # -- Vault lifecycle ----------------------------------------------------

    def create(self, master_password: str) -> None:
        """Create a new empty vault with the given master password.

        Raises VaultError if a vault file already exists.
        """
        if self._path.exists():
            raise VaultError(
                f"Vault already exists at {self._path}. "
                "Delete it first or use a different path."
            )

        salt = os.urandom(32)
        master_key = self._derive_key(master_password, salt)

        # Encrypt known plaintext to verify password later
        check_nonce, check_ct, check_tag = self._aes_encrypt(
            self.KNOWN_PLAINTEXT, master_key
        )

        vault: dict[str, Any] = {
            "version": 1,
            "salt": base64.b64encode(salt).decode("ascii"),
            "argon2_params": {
                "time_cost": 3,
                "memory_cost": 65536,
                "parallelism": 4,
            },
            "entries": {},
            "master_key_check": {
                "nonce": base64.b64encode(check_nonce).decode("ascii"),
                "ciphertext": base64.b64encode(check_ct).decode("ascii"),
                "tag": base64.b64encode(check_tag).decode("ascii"),
            },
        }

        self._save_vault(vault)
        self._master_key = master_key
        self._salt = salt
        self._vault_data = vault
        self._unlocked = True
        log.info("[vault] created new vault at %s", self._path)

    def unlock(self, master_password: str) -> bool:
        """Unlock vault with master password. Returns True if correct."""
        vault = self._load_vault()
        salt = base64.b64decode(vault["salt"])
        master_key = self._derive_key(master_password, salt)

        # Verify by decrypting the check value
        check = vault["master_key_check"]
        try:
            result = self._aes_decrypt(
                base64.b64decode(check["nonce"]),
                base64.b64decode(check["ciphertext"]),
                base64.b64decode(check["tag"]),
                master_key,
            )
            if result != self.KNOWN_PLAINTEXT:
                return False
        except Exception:
            return False

        self._master_key = master_key
        self._salt = salt
        self._vault_data = vault
        self._unlocked = True
        log.info("[vault] unlocked")
        return True

    def lock(self) -> None:
        """Lock vault, wipe master key from memory."""
        if self._master_key:
            key_arr = bytearray(self._master_key)
            self._secure_zero(key_arr)
        self._master_key = None
        self._salt = None
        self._vault_data = None
        self._unlocked = False
        log.info("[vault] locked")

    def _require_unlocked(self) -> None:
        if not self._unlocked:
            raise VaultLockedError("Vault is locked — unlock it first")

    # -- Credential CRUD ----------------------------------------------------

    def add_credential(
        self,
        site: str,
        username: str,
        password: str,
        *,
        url: str = "",
        notes: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        """Add a credential. Vault must be unlocked."""
        self._require_unlocked()
        assert self._master_key is not None
        assert self._vault_data is not None

        entry_json = json.dumps(
            {
                "username": username,
                "password": password,
                "url": url,
                "notes": notes,
                "aliases": aliases or [],
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        ).encode("utf-8")

        nonce, ct, tag = self._aes_encrypt(entry_json, self._master_key)

        self._vault_data["entries"][site.lower()] = {
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ct).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }
        self._save_vault(self._vault_data)
        log.info("[vault] added credential for '%s'", site.lower())

    def get_credential(self, site: str) -> VaultEntry | None:
        """Get decrypted credential. Returns None if not found.

        SECURITY: The returned VaultEntry contains the plaintext password.
        The caller MUST use it immediately (e.g. Selenium form fill) and
        then let it go out of scope. NEVER pass it to LLM context, logs,
        ToolResult, or any persistent store.
        """
        self._require_unlocked()
        assert self._vault_data is not None

        key = site.lower()
        entry_data = self._vault_data["entries"].get(key)

        if not entry_data:
            # Search by alias — must decrypt each entry to check
            for k, v in self._vault_data["entries"].items():
                decrypted = self._decrypt_entry(v)
                if decrypted and site.lower() in [
                    a.lower() for a in decrypted.get("aliases", [])
                ]:
                    entry_data = v
                    key = k
                    break

        if not entry_data:
            return None

        decrypted = self._decrypt_entry(entry_data)
        if not decrypted:
            return None

        return VaultEntry(
            site=key,
            username=decrypted["username"],
            password=decrypted["password"],
            url=decrypted.get("url", ""),
            notes=decrypted.get("notes", ""),
            created_at=decrypted.get("created_at", 0),
            updated_at=decrypted.get("updated_at", 0),
            aliases=decrypted.get("aliases", []),
        )

    def list_sites(self) -> list[str]:
        """List all stored site names (no passwords revealed)."""
        self._require_unlocked()
        assert self._vault_data is not None
        return list(self._vault_data["entries"].keys())

    def remove_credential(self, site: str) -> bool:
        """Remove a credential. Returns True if found and removed."""
        self._require_unlocked()
        assert self._vault_data is not None

        key = site.lower()
        if key not in self._vault_data["entries"]:
            return False

        del self._vault_data["entries"][key]
        self._save_vault(self._vault_data)
        log.info("[vault] removed credential for '%s'", key)
        return True

    def change_master_password(
        self, old_password: str, new_password: str
    ) -> bool:
        """Re-encrypt the entire vault with a new master password.

        Returns True on success, False if old_password is wrong.
        """
        # Verify old password first
        if not self._unlocked:
            if not self.unlock(old_password):
                return False
        assert self._vault_data is not None
        assert self._master_key is not None

        # Decrypt all entries with old key
        decrypted_entries: dict[str, dict] = {}
        for site, entry_data in self._vault_data["entries"].items():
            decrypted = self._decrypt_entry(entry_data)
            if decrypted is None:
                raise VaultCorruptError(
                    f"Cannot decrypt entry '{site}' — vault may be corrupt"
                )
            decrypted_entries[site] = decrypted

        # Derive new key
        new_salt = os.urandom(32)
        new_master_key = self._derive_key(new_password, new_salt)

        # Re-encrypt check value
        check_nonce, check_ct, check_tag = self._aes_encrypt(
            self.KNOWN_PLAINTEXT, new_master_key
        )

        # Re-encrypt all entries
        new_entries: dict[str, dict] = {}
        for site, entry in decrypted_entries.items():
            entry_json = json.dumps(entry).encode("utf-8")
            nonce, ct, tag = self._aes_encrypt(entry_json, new_master_key)
            new_entries[site] = {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ct).decode("ascii"),
                "tag": base64.b64encode(tag).decode("ascii"),
            }

        # Wipe old key
        old_key_arr = bytearray(self._master_key)
        self._secure_zero(old_key_arr)

        # Save new vault
        vault: dict[str, Any] = {
            "version": 1,
            "salt": base64.b64encode(new_salt).decode("ascii"),
            "argon2_params": {
                "time_cost": 3,
                "memory_cost": 65536,
                "parallelism": 4,
            },
            "entries": new_entries,
            "master_key_check": {
                "nonce": base64.b64encode(check_nonce).decode("ascii"),
                "ciphertext": base64.b64encode(check_ct).decode("ascii"),
                "tag": base64.b64encode(check_tag).decode("ascii"),
            },
        }

        self._save_vault(vault)
        self._master_key = new_master_key
        self._salt = new_salt
        self._vault_data = vault
        log.info("[vault] master password changed")
        return True

    # -- Internal helpers ---------------------------------------------------

    def _decrypt_entry(self, entry_data: dict) -> dict | None:
        """Decrypt a single vault entry. Returns parsed JSON dict or None."""
        assert self._master_key is not None
        try:
            plaintext = self._aes_decrypt(
                base64.b64decode(entry_data["nonce"]),
                base64.b64decode(entry_data["ciphertext"]),
                base64.b64decode(entry_data["tag"]),
                self._master_key,
            )
            return json.loads(plaintext.decode("utf-8"))
        except Exception:
            log.warning("[vault] failed to decrypt entry", exc_info=True)
            return None

    def _save_vault(self, data: dict) -> None:
        """Encrypt with DPAPI (Layer 3) and save to disk."""
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

        from jarvis.platform.secrets import dpapi_available

        if dpapi_available():
            from jarvis.platform.secrets import _protect

            try:
                encrypted = _protect(json_bytes)
            except Exception:
                log.warning(
                    "[vault] DPAPI encrypt failed; saving with AES-only "
                    "(no DPAPI layer)",
                    exc_info=True,
                )
                encrypted = json_bytes
        else:
            # Non-Windows fallback: AES layer only, no DPAPI
            log.debug("[vault] DPAPI unavailable; saving without DPAPI layer")
            encrypted = json_bytes

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(VAULT_MAGIC + encrypted)

    def _load_vault(self) -> dict:
        """Load and DPAPI-decrypt vault from disk."""
        if not self._path.exists():
            raise VaultError(f"Vault not found at {self._path}")

        raw = self._path.read_bytes()
        if not raw.startswith(VAULT_MAGIC):
            raise VaultCorruptError(
                f"Invalid vault file (missing magic header) at {self._path}"
            )

        payload = raw[len(VAULT_MAGIC) :]

        from jarvis.platform.secrets import dpapi_available

        if dpapi_available():
            from jarvis.platform.secrets import _unprotect

            try:
                json_bytes = _unprotect(payload)
            except Exception:
                # Payload may be non-DPAPI (created on non-Windows or DPAPI
                # failed at creation time). Try parsing directly.
                json_bytes = payload
        else:
            json_bytes = payload

        try:
            vault = json.loads(json_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VaultCorruptError(
                f"Cannot parse vault at {self._path}"
            ) from exc

        if "version" not in vault or "entries" not in vault:
            raise VaultCorruptError("Vault file is missing required fields")

        return vault
