"""Column-level DPAPI encryption for SQLite databases.

Strategy: encrypt individual TEXT column values, leaving schema, indexes,
and non-sensitive metadata (timestamps, counters, tiers) in plaintext so
that SQLite indexes and FTS triggers continue to work normally.

This is intentionally NOT whole-database encryption (that requires
SQLCipher, a separate native library).  Column-level encryption is
simpler, has zero extra dependencies, and matches the threat model:
protect the *content* of user memories while keeping the structural
metadata queryable.

Usage in MemoryStore / RelationStore::

    from jarvis.platform.encrypted_db import encrypt_value, decrypt_value

    # Before INSERT:
    conn.execute("INSERT INTO facts (text, ...) VALUES (?, ...)",
                 (encrypt_value(text), ...))

    # After SELECT:
    row = conn.execute("SELECT text, ... FROM facts WHERE id = ?", (fid,))
    plaintext = decrypt_value(row[0])

Encrypted values use the same ``dpapi:<base64>`` format as secrets.py so
that the "is this encrypted?" check is a simple prefix test and migration
of legacy plaintext rows is automatic — ``decrypt_value`` on a non-prefixed
string returns it unchanged, and the next write encrypts it.

Non-Windows: passthrough with one-per-process warning (same as secrets.py
and encrypted_store.py).
"""

from __future__ import annotations

import logging

from jarvis.platform.secrets import (
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)

log = logging.getLogger(__name__)


def encrypt_value(value: str | None) -> str | None:
    """Encrypt a text column value for storage in SQLite.

    - None → None (NULL columns stay NULL).
    - Empty string → empty string (no point encrypting nothing).
    - Already-encrypted → returned unchanged (idempotent).
    - On non-Windows: plaintext passthrough with warning.
    """
    if value is None or value == "":
        return value
    # encrypt_secret is already idempotent (skips dpapi: prefix)
    result = encrypt_secret(value, setting="db_column")
    # encrypt_secret may return non-str on edge cases; coerce.
    return str(result) if result is not None else value


def decrypt_value(value: str | None) -> str | None:
    """Decrypt a text column value read from SQLite.

    - None → None.
    - Non-encrypted string → returned as-is (legacy plaintext row).
    - ``dpapi:`` prefixed → decrypted.
    - On non-Windows with dpapi: prefix → returns empty string with warning.
    """
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        return value  # legacy plaintext — will be encrypted on next write
    result = decrypt_secret(value, setting="db_column")
    return str(result) if result is not None else ""


def needs_migration(value: str | None) -> bool:
    """True when *value* is a non-empty plaintext string that should be
    encrypted.  Used by the one-time migration pass to identify rows that
    predate column encryption."""
    if value is None or value == "":
        return False
    return not is_encrypted(value)
