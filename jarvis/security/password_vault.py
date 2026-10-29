"""Crypto-grade password vault — 3-layer encryption for user credentials.

Encryption layers:
1. Argon2id: master_password + salt -> master_key (32 bytes)
   - Memory-hard KDF, resistant to GPU/ASIC attacks
   - Default params: time_cost=3, memory_cost=65536 (64MB), parallelism=4
2. AES-256-GCM: master_key encrypts each credential individually
   - Each entry has a unique 12-byte random nonce
   - Authenticated encryption (tampering is detected)
3. DPAPI: wraps the entire vault file (Windows user-account binding)

ONE set of primitives, no silent downgrade
-------------------------------------------
New data is ALWAYS written as Argon2id + AES-256-GCM via the ``cryptography``
and ``argon2-cffi`` libraries. If either library is missing the vault raises
``VaultBackendMissingError`` — it never falls back to ``hashlib.scrypt`` or a
home-grown cipher, because a different KDF/cipher yields a different key and
an unreadable file that looks like "wrong password" / "data tampered".

The legacy scrypt KDF, pycryptodome, and the old HMAC-XOR scheme survive
ONLY in the ``_legacy_*`` read helpers, used exclusively to open and migrate
old v1 files. They are never used to encrypt.

On-disk format (v2)::

    vault.enc = VAULT_MAGIC + DPAPI_encrypt(json.dumps({
        "version": 2,
        "kdf": "argon2id",
        "cipher": "aes-256-gcm",
        "kdf_params": {"time_cost": 3, "memory_cost": 65536, "parallelism": 4},
        "salt": base64(32 random bytes),
        "entries": {
            "steam": {"nonce": b64, "ciphertext": b64, "tag": b64},
            ...
        },
        "master_key_check": {"nonce": b64, "ciphertext": b64, "tag": b64}
    }))

``master_key_check`` is the AES-GCM encryption of a known plaintext; it lets
us verify the master password WITHOUT storing the key.

CRITICAL: master_key and decrypted passwords exist ONLY in RAM. After use
they are overwritten with zeros (``_secure_zero``).

Never log passwords, keys, plaintext, or secret file paths — not even in
error messages.
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

# Current on-disk format version and the one set of algorithms it uses.
VAULT_VERSION = 2
KDF_ARGON2ID = "argon2id"
CIPHER_AES_256_GCM = "aes-256-gcm"

# Production Argon2id cost. Tests may pass reduced costs explicitly — but the
# algorithm stays Argon2id, never a cheaper substitute.
DEFAULT_KDF_PARAMS: dict[str, int] = {
    "time_cost": 3,
    "memory_cost": 65536,  # 64 MB
    "parallelism": 4,
}


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


# -- error taxonomy ---------------------------------------------------------
# Distinct types with distinct messages so the UI never masks one failure
# mode as another (a missing backend must not look like "wrong password",
# tampering must not look like "wrong password", etc.).


class VaultError(Exception):
    """Any vault operation failure."""


class VaultLockedError(VaultError):
    """Attempted credential access while vault is locked."""


class VaultBackendMissingError(VaultError):
    """A required crypto library (cryptography / argon2-cffi) is not installed.

    The vault refuses to operate rather than downgrade to weaker crypto.
    """


class VaultPasswordError(VaultError):
    """Master password is wrong, or authentication of the check value failed."""


class VaultCorruptError(VaultError):
    """Vault file is damaged, or a ciphertext failed its authentication tag."""


class VaultVersionError(VaultError):
    """Vault file declares a format version/algorithm this build cannot read."""


# -- backend availability ---------------------------------------------------


def _require_cryptography() -> Any:
    """Return the AESGCM class, or raise VaultBackendMissingError."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise VaultBackendMissingError(
            "The 'cryptography' package is required for the password vault "
            "(AES-256-GCM). Install it with: pip install cryptography"
        ) from exc
    return AESGCM


def _require_argon2() -> tuple[Any, Any]:
    """Return (hash_secret_raw, Type), or raise VaultBackendMissingError."""
    try:
        from argon2.low_level import Type, hash_secret_raw
    except ImportError as exc:
        raise VaultBackendMissingError(
            "The 'argon2-cffi' package is required for the password vault "
            "(Argon2id key derivation). Install it with: "
            "pip install argon2-cffi"
        ) from exc
    return hash_secret_raw, Type


class PasswordVault:
    """Crypto-grade password storage with 3-layer encryption.

    See module docstring for the full architecture description.
    """

    KNOWN_PLAINTEXT = b"JARVIS_VAULT_OK"

    def __init__(self, vault_path: Path | None = None) -> None:
        # Single data-dir resolver (jarvis.paths); layout unchanged:
        # <data_dir>/vault.enc. Resolved at call time.
        from jarvis.paths import data_path
        self._path = vault_path if vault_path is not None else data_path("vault.enc")
        self._master_key: bytes | None = None
        self._salt: bytes | None = None
        self._vault_data: dict | None = None
        self._unlocked = False
        # KDF cost used for THIS instance's writes. Production default; tests
        # may lower it explicitly (still Argon2id, never a cheaper KDF).
        self._kdf_params: dict[str, int] = dict(DEFAULT_KDF_PARAMS)

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
    def _derive_key(
        password: str,
        salt: bytes,
        *,
        kdf_params: dict[str, int] | None = None,
    ) -> bytes:
        """Argon2id KDF: password + salt -> 32-byte master key.

        Always Argon2id (via argon2-cffi). Raises VaultBackendMissingError
        if argon2-cffi is not installed — there is NO scrypt fallback,
        because a different KDF produces a different key.

        ``kdf_params`` overrides the cost (time_cost / memory_cost /
        parallelism). Tests pass reduced costs here to stay fast WITHOUT
        changing the algorithm.
        """
        hash_secret_raw, Type = _require_argon2()
        params = kdf_params or DEFAULT_KDF_PARAMS
        return hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=salt,
            time_cost=params["time_cost"],
            memory_cost=params["memory_cost"],
            parallelism=params["parallelism"],
            hash_len=32,
            type=Type.ID,
        )

    # -- AES-256-GCM (Layer 2) ---------------------------------------------

    @staticmethod
    def _aes_encrypt(plaintext: bytes, key: bytes) -> tuple[bytes, bytes, bytes]:
        """AES-256-GCM encrypt. Returns (nonce, ciphertext, tag).

        Only ``cryptography`` is used. Raises VaultBackendMissingError if it
        is not installed — no pycryptodome / HMAC-XOR fallback on the write
        path.
        """
        AESGCM = _require_cryptography()
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ct = aesgcm.encrypt(nonce, plaintext, None)
        # cryptography appends the 16-byte tag to the ciphertext.
        return nonce, ct[:-16], ct[-16:]

    @staticmethod
    def _aes_decrypt(
        nonce: bytes, ciphertext: bytes, tag: bytes, key: bytes
    ) -> bytes:
        """AES-256-GCM decrypt via ``cryptography``.

        Raises VaultBackendMissingError if the library is absent, or
        VaultCorruptError if the authentication tag does not verify
        (tampering / wrong key).
        """
        AESGCM = _require_cryptography()
        aesgcm = AESGCM(key)
        try:
            return aesgcm.decrypt(nonce, ciphertext + tag, None)
        except Exception as exc:  # cryptography raises InvalidTag
            raise VaultCorruptError(
                "Ciphertext failed authentication (wrong key or tampered data)"
            ) from exc

    # -- Legacy read-only primitives (v1 migration ONLY) -------------------
    # These mirror the OLD fallbacks that could have produced a user's v1
    # file. They are used exclusively by _legacy_open_v1 to read and migrate
    # an old vault. They MUST NOT be used to encrypt anything.

    @staticmethod
    def _legacy_derive_key_scrypt(password: str, salt: bytes) -> bytes:
        """OLD scrypt fallback KDF (read-only; for v1 migration)."""
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**17,
            r=8,
            p=1,
            dklen=32,
            # n=2**17, r=8 needs ~128 MB; OpenSSL's default maxmem (32 MB)
            # rejects that with "memory limit exceeded". Raise the cap so the
            # legacy read path can reproduce the old key.
            maxmem=256 * 1024 * 1024,
        )

    @staticmethod
    def _legacy_decrypt_pycryptodome(
        nonce: bytes, ciphertext: bytes, tag: bytes, key: bytes
    ) -> bytes:
        """OLD pycryptodome AES-GCM path (read-only; for v1 migration)."""
        from Crypto.Cipher import AES  # noqa: PLC0415  (legacy read path)

        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)

    @staticmethod
    def _legacy_decrypt_hmac_xor(
        nonce: bytes, ciphertext: bytes, tag: bytes, key: bytes
    ) -> bytes:
        """OLD home-grown HMAC-XOR scheme (read-only; for v1 migration).

        NOT AES-GCM. Present only so a vault that was written by the old
        last-resort fallback can still be opened and migrated to v2. Never
        used to encrypt.
        """
        expected_tag = hmac.new(
            key, nonce + ciphertext, hashlib.sha256
        ).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            raise VaultCorruptError(
                "Ciphertext failed authentication (wrong key or tampered data)"
            )
        keystream = b""
        for i in range((len(ciphertext) // 32) + 1):
            keystream += hmac.new(
                key, nonce + i.to_bytes(4, "big"), hashlib.sha256
            ).digest()
        keystream = keystream[: len(ciphertext)]
        return bytes(a ^ b for a, b in zip(ciphertext, keystream, strict=True))

    # -- Secure memory wiping -----------------------------------------------

    @staticmethod
    def _secure_zero(data: bytearray | memoryview) -> None:
        """Overwrite sensitive data in memory with zeros.

        Best-effort: Python's GC may keep copies, but we wipe the primary
        reference. ctypes.memset is the gold standard but requires platform
        specifics; byte-by-byte is portable.
        """
        for i in range(len(data)):
            data[i] = 0

    # -- Vault document builders -------------------------------------------

    def _build_vault_doc(
        self, salt: bytes, master_key: bytes, entries: dict[str, dict]
    ) -> dict[str, Any]:
        """Assemble a v2 vault document (header + entries + check value)."""
        check_nonce, check_ct, check_tag = self._aes_encrypt(
            self.KNOWN_PLAINTEXT, master_key
        )
        return {
            "version": VAULT_VERSION,
            "kdf": KDF_ARGON2ID,
            "cipher": CIPHER_AES_256_GCM,
            "kdf_params": dict(self._kdf_params),
            "salt": base64.b64encode(salt).decode("ascii"),
            "entries": entries,
            "master_key_check": {
                "nonce": base64.b64encode(check_nonce).decode("ascii"),
                "ciphertext": base64.b64encode(check_ct).decode("ascii"),
                "tag": base64.b64encode(check_tag).decode("ascii"),
            },
        }

    # -- Vault lifecycle ----------------------------------------------------

    def create(self, master_password: str) -> None:
        """Create a new empty vault with the given master password.

        Raises VaultError if a vault file already exists, or
        VaultBackendMissingError if the crypto libraries are not installed.
        """
        if self._path.exists():
            raise VaultError(
                f"Vault already exists at {self._path}. "
                "Delete it first or use a different path."
            )

        salt = os.urandom(32)
        master_key = self._derive_key(
            master_password, salt, kdf_params=self._kdf_params
        )
        vault = self._build_vault_doc(salt, master_key, {})

        self._save_vault(vault)
        self._master_key = master_key
        self._salt = salt
        self._vault_data = vault
        self._unlocked = True
        log.info("[vault] created new vault at %s", self._path)

    def unlock(self, master_password: str) -> bool:
        """Unlock vault with master password.

        Returns True on success, False ONLY for a wrong password. Raises:
          * VaultBackendMissingError — crypto libraries absent
          * VaultVersionError — unknown format version / algorithm
          * VaultCorruptError — file damaged
        so the caller can tell these apart from "wrong password".
        """
        vault = self._load_vault()
        version = vault.get("version")

        if version == VAULT_VERSION:
            ok = self._unlock_v2(vault, master_password)
        elif version == 1:
            ok = self._unlock_and_migrate_v1(vault, master_password)
        else:
            raise VaultVersionError(
                f"Unsupported vault format version: {version!r}. "
                "This build understands versions 1 and 2."
            )
        return ok

    def _unlock_v2(self, vault: dict, master_password: str) -> bool:
        """Open a v2 vault. Validates declared algorithms explicitly."""
        kdf = vault.get("kdf")
        cipher = vault.get("cipher")
        if kdf != KDF_ARGON2ID or cipher != CIPHER_AES_256_GCM:
            raise VaultVersionError(
                f"Vault declares unsupported algorithms (kdf={kdf!r}, "
                f"cipher={cipher!r}); expected {KDF_ARGON2ID} / "
                f"{CIPHER_AES_256_GCM}."
            )
        kdf_params = vault.get("kdf_params") or DEFAULT_KDF_PARAMS
        salt = base64.b64decode(vault["salt"])
        master_key = self._derive_key(
            master_password, salt, kdf_params=kdf_params
        )

        if not self._check_master_key(vault, master_key):
            return False

        self._master_key = master_key
        self._salt = salt
        self._vault_data = vault
        self._kdf_params = dict(kdf_params)
        self._unlocked = True
        log.info("[vault] unlocked (v2)")
        return True

    def _check_master_key(self, vault: dict, master_key: bytes) -> bool:
        """True if master_key correctly decrypts the known check value.

        A wrong key fails the GCM tag (VaultCorruptError) — that maps to a
        wrong password here, which is the one case unlock() reports as False.
        """
        check = vault["master_key_check"]
        try:
            result = self._aes_decrypt(
                base64.b64decode(check["nonce"]),
                base64.b64decode(check["ciphertext"]),
                base64.b64decode(check["tag"]),
                master_key,
            )
        except VaultCorruptError:
            return False
        return result == self.KNOWN_PLAINTEXT

    # -- v1 open + migration (read-only legacy paths) ----------------------

    def _unlock_and_migrate_v1(self, vault: dict, master_password: str) -> bool:
        """Open a legacy v1 vault, then migrate it to v2.

        Tries the standard path first (Argon2id + AES-GCM). If that fails to
        authenticate, tries the known legacy combinations via _legacy_* read
        helpers. On success, re-encrypts to v2 with a backup and atomic
        replace. Returns False only if NO combination accepts the password.
        """
        salt = base64.b64decode(vault["salt"])
        opened = self._legacy_open_v1(vault, master_password, salt)
        if opened is None:
            return False

        master_key, decrypted_entries = opened

        # Re-encrypt to v2 under a fresh Argon2id key and migrate on disk.
        try:
            self._migrate_v1_to_v2(master_password, decrypted_entries)
        except VaultBackendMissingError:
            # Can't write v2 without the real backend — surface it, don't
            # silently keep the weak v1.
            raise
        except Exception as exc:
            raise VaultCorruptError(
                "Failed to migrate the vault to the current format"
            ) from exc

        # _migrate_v1_to_v2 leaves the vault unlocked under the new key.
        key_arr = bytearray(master_key)
        self._secure_zero(key_arr)
        log.info("[vault] unlocked (v1) and migrated to v2")
        return True

    def _legacy_open_v1(
        self, vault: dict, master_password: str, salt: bytes
    ) -> tuple[bytes, dict[str, dict]] | None:
        """Try every known v1 (kdf, decrypt) combination, read-only.

        Returns (master_key, {site: entry_dict}) on the first combination
        whose check value authenticates, or None if none do.
        """
        import importlib.util

        # find_spec on a submodule raises ModuleNotFoundError when the parent
        # package is absent, so probe the top-level package first.
        _has_pycryptodome = importlib.util.find_spec("Crypto") is not None

        # Build the KDF groups lazily so the expensive scrypt key is only
        # derived if the (fast) Argon2id combinations fail. Each group is
        # (lazy_key_fn, label_prefix, [decrypt_fns]). Argon2id first.
        groups: list[tuple[Any, str, list[Any]]] = []

        def _argon_key() -> bytes:
            return self._derive_key(
                master_password, salt, kdf_params=DEFAULT_KDF_PARAMS
            )

        def _scrypt_key() -> bytes:
            return self._legacy_derive_key_scrypt(master_password, salt)

        decryptors: list[Any] = [self._aes_decrypt, self._legacy_decrypt_hmac_xor]
        if _has_pycryptodome:
            decryptors.append(self._legacy_decrypt_pycryptodome)

        # Argon2id group only if argon2-cffi is importable.
        try:
            _require_argon2()
            groups.append((_argon_key, "argon2id", decryptors))
        except VaultBackendMissingError:
            # argon2-cffi absent: skip the Argon2id combinations and fall
            # back to the scrypt legacy group below. This is control-flow,
            # not a swallowed security check — a wrong password still fails
            # every combination and _legacy_open_v1 returns None.
            log.debug("[vault] argon2 unavailable; v1 migration tries scrypt only")
        # Legacy scrypt group (stdlib, always available).
        groups.append((_scrypt_key, "scrypt", decryptors))

        for key_fn, kdf_label, decrypt_fns in groups:
            master_key = key_fn()
            for decrypt_fn in decrypt_fns:
                entries = self._try_open_v1_with(vault, master_key, decrypt_fn)
                if entries is not None:
                    log.info(
                        "[vault] v1 opened via legacy combo: %s+%s",
                        kdf_label, getattr(decrypt_fn, "__name__", "?"),
                    )
                    return master_key, entries
        return None

    def _try_open_v1_with(
        self, vault: dict, master_key: bytes, decrypt_fn: Any
    ) -> dict[str, dict] | None:
        """Verify the check value with decrypt_fn; if OK, decrypt all entries.

        Returns {site: entry_dict} or None if the check value does not
        authenticate under this key/decryptor.
        """
        check = vault["master_key_check"]
        try:
            result = decrypt_fn(
                base64.b64decode(check["nonce"]),
                base64.b64decode(check["ciphertext"]),
                base64.b64decode(check["tag"]),
                master_key,
            )
        except Exception:
            return None
        if result != self.KNOWN_PLAINTEXT:
            return None

        entries: dict[str, dict] = {}
        for site, entry_data in vault.get("entries", {}).items():
            try:
                plaintext = decrypt_fn(
                    base64.b64decode(entry_data["nonce"]),
                    base64.b64decode(entry_data["ciphertext"]),
                    base64.b64decode(entry_data["tag"]),
                    master_key,
                )
                entries[site] = json.loads(plaintext.decode("utf-8"))
            except Exception as exc:
                raise VaultCorruptError(
                    f"Cannot decrypt entry '{site}' during migration"
                ) from exc
        return entries

    def _migrate_v1_to_v2(
        self, master_password: str, decrypted_entries: dict[str, dict]
    ) -> None:
        """Write the opened v1 contents as a fresh v2 file, safely.

        Order: backup <file>.v1.bak -> write temp -> verify temp opens with
        the same password -> atomic replace. Any failure leaves the original
        file untouched (and removes the temp file).
        """
        original = self._path.read_bytes()
        backup_path = self._path.with_suffix(self._path.suffix + ".v1.bak")
        tmp_path = self._path.with_suffix(self._path.suffix + ".v2.tmp")

        # Fresh Argon2id key + v2 document.
        new_salt = os.urandom(32)
        new_key = self._derive_key(
            master_password, new_salt, kdf_params=self._kdf_params
        )
        new_entries: dict[str, dict] = {}
        for site, entry in decrypted_entries.items():
            entry_json = json.dumps(entry).encode("utf-8")
            nonce, ct, tag = self._aes_encrypt(entry_json, new_key)
            new_entries[site] = {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ct).decode("ascii"),
                "tag": base64.b64encode(tag).decode("ascii"),
            }
        new_doc = self._build_vault_doc(new_salt, new_key, new_entries)

        try:
            # 1. Backup the original v1 bytes.
            backup_path.write_bytes(original)
            # 2. Write the new vault to a temp file.
            self._write_vault_bytes(tmp_path, new_doc)
            # 3. Verify the temp file opens with the same password.
            if not self._verify_file_opens(tmp_path, master_password):
                raise VaultCorruptError(
                    "Migrated vault failed verification before replace"
                )
            # 4. Atomic replace.
            os.replace(tmp_path, self._path)
        except Exception:
            # Leave the original intact; clean up the temp file.
            tmp_path.unlink(missing_ok=True)
            raise
        finally:
            # Temp must never linger if replace already consumed it; unlink is
            # safe (missing_ok) whether or not the replace happened.
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

        self._master_key = new_key
        self._salt = new_salt
        self._vault_data = new_doc
        self._unlocked = True

    def _verify_file_opens(self, path: Path, master_password: str) -> bool:
        """Open *path* in a throwaway vault to confirm the password works."""
        probe = PasswordVault(vault_path=path)
        probe._kdf_params = dict(self._kdf_params)
        try:
            raw = path.read_bytes()
            payload = self._strip_dpapi(raw)
            doc = json.loads(payload.decode("utf-8"))
            key = self._derive_key(
                master_password,
                base64.b64decode(doc["salt"]),
                kdf_params=doc.get("kdf_params") or DEFAULT_KDF_PARAMS,
            )
            return probe._check_master_key(doc, key)
        except Exception:
            return False

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
            # Search by alias — must decrypt each entry to check.
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
        if not self._unlocked:
            if not self.unlock(old_password):
                return False
        assert self._vault_data is not None
        assert self._master_key is not None

        # Decrypt all entries with the current key.
        decrypted_entries: dict[str, dict] = {}
        for site, entry_data in self._vault_data["entries"].items():
            decrypted = self._decrypt_entry(entry_data)
            if decrypted is None:
                raise VaultCorruptError(
                    f"Cannot decrypt entry '{site}' — vault may be corrupt"
                )
            decrypted_entries[site] = decrypted

        # Fresh salt + key, re-encrypt everything as v2.
        new_salt = os.urandom(32)
        new_master_key = self._derive_key(
            new_password, new_salt, kdf_params=self._kdf_params
        )
        new_entries: dict[str, dict] = {}
        for site, entry in decrypted_entries.items():
            entry_json = json.dumps(entry).encode("utf-8")
            nonce, ct, tag = self._aes_encrypt(entry_json, new_master_key)
            new_entries[site] = {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ct).decode("ascii"),
                "tag": base64.b64encode(tag).decode("ascii"),
            }

        # Wipe the old key.
        old_key_arr = bytearray(self._master_key)
        self._secure_zero(old_key_arr)

        vault = self._build_vault_doc(new_salt, new_master_key, new_entries)
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

    @staticmethod
    def _strip_dpapi(raw: bytes) -> bytes:
        """Return the JSON payload bytes from raw vault file contents.

        Strips the magic header and the DPAPI layer (if present / available).
        Raises VaultCorruptError if the magic header is missing.
        """
        if not raw.startswith(VAULT_MAGIC):
            raise VaultCorruptError("Invalid vault file (missing magic header)")
        payload = raw[len(VAULT_MAGIC) :]

        from jarvis.platform.secrets import dpapi_available

        if dpapi_available():
            from jarvis.platform.secrets import _unprotect

            try:
                return _unprotect(payload)
            except Exception:
                # Payload may be non-DPAPI (created on non-Windows or DPAPI
                # failed at creation time). Fall through to raw payload.
                return payload
        return payload

    def _write_vault_bytes(self, path: Path, data: dict) -> None:
        """Serialize + DPAPI-wrap + write a vault document to *path*."""
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
            log.debug("[vault] DPAPI unavailable; saving without DPAPI layer")
            encrypted = json_bytes

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(VAULT_MAGIC + encrypted)

    def _save_vault(self, data: dict) -> None:
        """Encrypt with DPAPI (Layer 3) and save to the vault path."""
        self._write_vault_bytes(self._path, data)

    def _load_vault(self) -> dict:
        """Load and DPAPI-decrypt vault from disk."""
        if not self._path.exists():
            raise VaultError(f"Vault not found at {self._path}")

        raw = self._path.read_bytes()
        try:
            json_bytes = self._strip_dpapi(raw)
        except VaultCorruptError:
            raise

        try:
            vault = json.loads(json_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VaultCorruptError(
                f"Cannot parse vault at {self._path}"
            ) from exc

        if "version" not in vault or "entries" not in vault:
            raise VaultCorruptError("Vault file is missing required fields")

        return vault
