"""Integration tests: verify all sensitive data is encrypted after save.

These tests exercise the full data-path through MemoryStore, UserProfile,
PersonalAliasStore, ToolExampleStore, and UsageStats to verify that on-disk
content is encrypted (not readable plaintext) when DPAPI is available.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.platform.encrypted_store import MAGIC_HEADER
from jarvis.platform.secrets import SCHEME_PREFIX


# ---------------------------------------------------------------------------
# Shared DPAPI mocks
# ---------------------------------------------------------------------------


def _fake_protect(data: bytes) -> bytes:
    return bytes(b ^ 0x42 for b in data)


def _fake_unprotect(blob: bytes) -> bytes:
    return bytes(b ^ 0x42 for b in blob)


@pytest.fixture()
def dpapi_mock():
    """Enable fake DPAPI for the whole test."""
    with (
        patch("jarvis.platform.secrets.dpapi_available", return_value=True),
        patch("jarvis.platform.secrets._protect", side_effect=_fake_protect),
        patch("jarvis.platform.secrets._unprotect", side_effect=_fake_unprotect),
    ):
        yield


# ---------------------------------------------------------------------------
# JSON file encryption tests
# ---------------------------------------------------------------------------


class TestJsonFilesEncrypted:
    """Verify that JSON-based stores write encrypted files."""

    def test_user_profile_encrypted(self, tmp_path: Path, dpapi_mock):
        from jarvis.memory.user_profile import UserProfile

        path = tmp_path / "profile.json"
        profile = UserProfile(path)
        profile.set("name", "Serhii")

        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER
        # Plaintext name must NOT appear in file
        assert b"Serhii" not in raw

    def test_personal_aliases_encrypted(self, tmp_path: Path, dpapi_mock):
        from jarvis.memory.personal_aliases import PersonalAliasStore

        path = tmp_path / "aliases.json"
        store = PersonalAliasStore(path)
        store.learn("браузер", "chrome")

        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER
        assert b"chrome" not in raw

    def test_tool_examples_encrypted(self, tmp_path: Path, dpapi_mock):
        from jarvis.memory.tool_examples import ToolExampleStore

        path = tmp_path / "examples.json"
        store = ToolExampleStore(path)
        store.record_success("открой хром", "open_app", {"app_name": "chrome"})

        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER

    def test_usage_stats_encrypted(self, tmp_path: Path, dpapi_mock):
        from jarvis.memory.usage_stats import UsageStats

        path = tmp_path / "stats.json"
        stats = UsageStats(path)
        stats.record_tool_use("open_app")

        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER


# ---------------------------------------------------------------------------
# SQLite column encryption tests
# ---------------------------------------------------------------------------


class TestSqliteColumnsEncrypted:
    """Verify that MemoryStore encrypts sensitive columns."""

    async def test_fact_text_encrypted_in_db(self, tmp_path: Path, dpapi_mock):
        """After add(), the text column in SQLite contains dpapi: ciphertext."""
        from jarvis.memory.store import MemoryStore

        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path=db_path)
        await store.open()
        fact_id = await store.add("My secret fact", category="personal", tags=["test"])

        # Read raw from SQLite to verify encryption
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT text FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        raw_text = row[0]
        # The raw value in DB must be encrypted (dpapi: prefix)
        assert raw_text.startswith(SCHEME_PREFIX), (
            f"Expected encrypted text starting with '{SCHEME_PREFIX}', "
            f"got: {raw_text[:50]}"
        )
        # Original plaintext must NOT be in the raw value
        assert "My secret fact" not in raw_text

    async def test_fact_text_decrypted_on_get(self, tmp_path: Path, dpapi_mock):
        """get() returns decrypted plaintext."""
        from jarvis.memory.store import MemoryStore

        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path=db_path)
        await store.open()
        fact_id = await store.add("Decrypted test", category="test")
        fact = await store.get(fact_id)

        assert fact is not None
        assert fact.text == "Decrypted test"

    async def test_fact_tags_encrypted_in_db(self, tmp_path: Path, dpapi_mock):
        """Tags are encrypted in the fact_tags table."""
        from jarvis.memory.store import MemoryStore

        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path=db_path)
        await store.open()
        fact_id = await store.add("Tag test", tags=["secret_tag"])

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT tag FROM fact_tags WHERE fact_id = ?", (fact_id,)
        ).fetchall()
        conn.close()

        # At least one tag should be encrypted
        assert len(rows) > 0
        for (tag_val,) in rows:
            if "secret_tag" in str(tag_val):
                # If tag isn't encrypted, the raw value contains plaintext
                # This should not happen when DPAPI is active
                assert tag_val.startswith(SCHEME_PREFIX), (
                    f"Tag should be encrypted, got: {tag_val}"
                )


# ---------------------------------------------------------------------------
# Legacy migration test
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    """Verify legacy plaintext data migrates transparently."""

    def test_legacy_json_auto_encrypts_on_save(
        self, tmp_path: Path, dpapi_mock,
    ):
        """A plaintext JSON file is auto-encrypted when the store saves."""
        from jarvis.memory.user_profile import UserProfile

        path = tmp_path / "legacy_profile.json"
        # Write legacy plaintext
        path.write_text(
            json.dumps({"name": "Serhii", "city": "ZG"}),
            encoding="utf-8",
        )

        # Load via UserProfile (should read plaintext fine)
        profile = UserProfile(path)
        assert profile.get("name") == "Serhii"

        # Trigger save → should encrypt
        profile.set("language", "uk")
        raw = path.read_bytes()
        assert raw[:len(MAGIC_HEADER)] == MAGIC_HEADER
        assert b"Serhii" not in raw
