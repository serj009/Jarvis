"""Encrypted JSON file helper — DPAPI encryption at rest for JSON data.

Wraps read/write of JSON files so that on-disk content is encrypted with
Windows DPAPI (same key-management story as ``secrets.py``). The module
is a *storage* concern — callers never see ciphertext.

File format
-----------
- First 8 bytes: magic header ``JRVS_ENC`` (ASCII).
- Remaining bytes: base64-encoded DPAPI blob of the UTF-8 JSON payload.

Legacy handling (backwards-compatible)
--------------------------------------
If the file starts with ``{`` or ``[`` it is *plaintext* JSON written
before this feature existed.  ``load()`` reads it as-is; the next
``save()`` will transparently re-write it encrypted.

Non-Windows fallback
--------------------
Exactly the same deal as ``secrets.py``: off-Windows the file is written
and read as plain JSON with a one-per-process warning.  Nobody is worse
off than before, but it is a real gap and it is logged.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Magic header written at the start of every encrypted file.
# Changing this would strand every encrypted file — don't.
MAGIC_HEADER = b"JRVS_ENC"

# One-per-process warning (mirrors secrets.py pattern).
_warned_plaintext_fallback = False


def _warn_plaintext_once() -> None:
    global _warned_plaintext_fallback
    if _warned_plaintext_fallback:
        return
    _warned_plaintext_fallback = True
    log.warning(
        "Windows DPAPI unavailable on %s — JSON data files are stored in "
        "PLAINTEXT on disk. This is the documented non-Windows fallback.",
        sys.platform,
    )


def _reset_warning_state() -> None:
    """Test hook: re-arm the one-time plaintext warning."""
    global _warned_plaintext_fallback
    _warned_plaintext_fallback = False


class EncryptedJsonStore:
    """Read/write JSON files with DPAPI encryption at rest.

    Usage::

        store = EncryptedJsonStore(Path("~/.jarvis/data/bookmarks.json"))
        data = store.load(default=[])
        data.append({"url": "https://..."})
        store.save(data)
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    # -- public API ---------------------------------------------------------

    def load(self, default: Any = None) -> Any:
        """Load and decrypt a JSON file.

        Handles three cases:
        1. File does not exist → return *default* (or ``{}``).
        2. File is legacy plaintext JSON → parse and return as-is.
        3. File is encrypted → decrypt and return parsed JSON.

        Never raises on missing/corrupt files — returns *default* with a
        warning so the caller can continue.
        """
        if default is None:
            default = {}
        if not self.path.exists():
            return default

        try:
            raw = self.path.read_bytes()
        except OSError:
            log.warning(
                "[encrypted-store] cannot read %s; returning default",
                self.path,
                exc_info=True,
            )
            return default

        if not raw:
            return default

        # Case 2: legacy plaintext JSON (starts with { or [)
        first_byte = raw[:1]
        if first_byte in (b"{", b"["):
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning(
                    "[encrypted-store] corrupt plaintext JSON in %s; "
                    "returning default",
                    self.path,
                    exc_info=True,
                )
                return default

        # Case 3: encrypted file (starts with JRVS_ENC)
        if raw[:len(MAGIC_HEADER)] == MAGIC_HEADER:
            payload_b64 = raw[len(MAGIC_HEADER):]
            try:
                plaintext_bytes = self._decrypt(
                    base64.b64decode(payload_b64, validate=True)
                )
                return json.loads(plaintext_bytes.decode("utf-8"))
            except Exception:
                log.warning(
                    "[encrypted-store] cannot decrypt %s — file may have "
                    "been saved by a different user account or on a "
                    "different machine; returning default",
                    self.path,
                    exc_info=True,
                )
                return default

        # Unknown format
        log.warning(
            "[encrypted-store] unrecognised file format for %s; "
            "returning default",
            self.path,
        )
        return default

    def save(self, data: Any) -> None:
        """Encrypt and save JSON data to disk.

        Creates parent directories as needed.  On non-Windows, writes
        plain JSON with a warning (same fallback as secrets.py).
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            plaintext_bytes = json.dumps(
                data, ensure_ascii=False, indent=2
            ).encode("utf-8")
            encrypted = self._encrypt(plaintext_bytes)
            self.path.write_bytes(MAGIC_HEADER + base64.b64encode(encrypted))
        except OSError:
            log.warning(
                "[encrypted-store] failed to save %s",
                self.path,
                exc_info=True,
            )

    # -- DPAPI wrappers -----------------------------------------------------

    @staticmethod
    def _encrypt(plaintext_bytes: bytes) -> bytes:
        """DPAPI encrypt (Windows) or passthrough (non-Windows)."""
        from jarvis.platform.secrets import _protect, dpapi_available

        if not dpapi_available():
            _warn_plaintext_once()
            return plaintext_bytes
        try:
            return _protect(plaintext_bytes)
        except Exception as exc:
            log.warning(
                "[encrypted-store] DPAPI encrypt failed (%s); "
                "storing plaintext fallback",
                exc,
            )
            return plaintext_bytes

    @staticmethod
    def _decrypt(encrypted_bytes: bytes) -> bytes:
        """DPAPI decrypt (Windows) or passthrough (non-Windows)."""
        from jarvis.platform.secrets import _unprotect, dpapi_available

        if not dpapi_available():
            _warn_plaintext_once()
            return encrypted_bytes
        return _unprotect(encrypted_bytes)
