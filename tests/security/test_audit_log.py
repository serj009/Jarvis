"""Tests for Layer 4 — SecurityAuditLog.

Covers:
- Logging tool calls
- Logging blocked actions
- Logging leak warnings
- Logging sanitizer triggers
- Logging session start/stop
- Hash chain verification (valid chain)
- Hash chain tampering detection (modified entry → verify fails)
- Encrypted data column (DPAPI)
- Recent entries retrieval
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.security.audit_log import _GENESIS_HASH, SecurityAuditLog

# ---------------------------------------------------------------------------
# Shared DPAPI mocks (same pattern as test_data_encryption.py)
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


@pytest.fixture()
def audit_log(tmp_path: Path) -> SecurityAuditLog:
    """Fresh audit log with plaintext data column (no DPAPI)."""
    return SecurityAuditLog(db_path=tmp_path / "test_audit.db")


@pytest.fixture()
def encrypted_audit_log(tmp_path: Path, dpapi_mock) -> SecurityAuditLog:
    """Fresh audit log with fake-DPAPI-encrypted data column."""
    return SecurityAuditLog(db_path=tmp_path / "test_audit_enc.db")


# ---------------------------------------------------------------------------
# 1. Logging tool calls
# ---------------------------------------------------------------------------


class TestLogToolCall:
    """log_tool_call() records tool execution events."""

    def test_log_tool_call(self, audit_log: SecurityAuditLog):
        audit_log.log_tool_call(
            tool_name="open_url",
            args_summary="url=https://google.com",
            origin="llm",
            result_summary="success",
        )
        entries = audit_log.get_recent(1)
        assert len(entries) == 1
        assert entries[0]["event_type"] == "tool_call"
        data = entries[0]["data"]
        assert data["data"]["tool_name"] == "open_url"
        assert data["data"]["origin"] == "llm"

    def test_multiple_tool_calls(self, audit_log: SecurityAuditLog):
        for i in range(5):
            audit_log.log_tool_call(
                tool_name=f"tool_{i}",
                args_summary=f"arg={i}",
                origin="llm",
                result_summary="ok",
            )
        entries = audit_log.get_recent(10)
        assert len(entries) == 5


# ---------------------------------------------------------------------------
# 2. Logging blocked actions
# ---------------------------------------------------------------------------


class TestLogBlocked:
    """log_blocked() records denied tool executions."""

    def test_log_blocked(self, audit_log: SecurityAuditLog):
        audit_log.log_blocked(
            tool_name="open_url",
            reason="suspicious URL blob",
            details="base64 payload detected",
        )
        entries = audit_log.get_recent(1)
        assert len(entries) == 1
        assert entries[0]["event_type"] == "blocked"
        data = entries[0]["data"]
        assert data["data"]["tool_name"] == "open_url"
        assert "suspicious" in data["data"]["reason"]


# ---------------------------------------------------------------------------
# 3. Logging leak warnings
# ---------------------------------------------------------------------------


class TestLogLeakWarning:
    """log_leak_warning() records data-leak detection events."""

    def test_log_leak_warning(self, audit_log: SecurityAuditLog):
        audit_log.log_leak_warning(
            url="https://evil.com/exfil",
            matched_data="user_email",
            origin="llm_tool_call",
        )
        entries = audit_log.get_recent(1)
        assert len(entries) == 1
        assert entries[0]["event_type"] == "leak_warning"


# ---------------------------------------------------------------------------
# 4. Logging sanitizer triggers
# ---------------------------------------------------------------------------


class TestLogSanitizerTrigger:
    """log_sanitizer_trigger() records pattern matches."""

    def test_log_sanitizer_trigger(self, audit_log: SecurityAuditLog):
        audit_log.log_sanitizer_trigger(
            pattern_name="system_tag",
            content_preview="[SYSTEM] Override...",
            source="llm_response",
        )
        entries = audit_log.get_recent(1)
        assert len(entries) == 1
        assert entries[0]["event_type"] == "sanitizer"
        data = entries[0]["data"]
        assert data["data"]["pattern_name"] == "system_tag"

    def test_content_preview_truncated(self, audit_log: SecurityAuditLog):
        """Long content previews are truncated to 200 chars."""
        long_content = "x" * 500
        audit_log.log_sanitizer_trigger(
            pattern_name="test",
            content_preview=long_content,
            source="test",
        )
        entries = audit_log.get_recent(1)
        preview = entries[0]["data"]["data"]["content_preview"]
        assert len(preview) <= 200


# ---------------------------------------------------------------------------
# 5. Session logging
# ---------------------------------------------------------------------------


class TestLogSession:
    """log_session() records session boundaries."""

    def test_log_session_start(self, audit_log: SecurityAuditLog):
        audit_log.log_session("start")
        entries = audit_log.get_recent(1)
        assert len(entries) == 1
        assert entries[0]["event_type"] == "session"
        assert entries[0]["data"]["data"]["event"] == "start"

    def test_log_session_stop(self, audit_log: SecurityAuditLog):
        audit_log.log_session("stop")
        entries = audit_log.get_recent(1)
        assert entries[0]["data"]["data"]["event"] == "stop"


# ---------------------------------------------------------------------------
# 6. Hash chain verification — valid chain
# ---------------------------------------------------------------------------


class TestVerifyChainValid:
    """verify_chain() returns True on an intact chain."""

    def test_empty_log_valid(self, audit_log: SecurityAuditLog):
        assert audit_log.verify_chain() is True

    def test_single_entry_valid(self, audit_log: SecurityAuditLog):
        audit_log.log_session("start")
        assert audit_log.verify_chain() is True

    def test_multi_entry_chain_valid(self, audit_log: SecurityAuditLog):
        audit_log.log_session("start")
        audit_log.log_tool_call("open_app", "app=chrome", "llm", "ok")
        audit_log.log_blocked("open_url", "blob detected", "base64 payload")
        audit_log.log_sanitizer_trigger("system_tag", "[SYSTEM]...", "llm")
        audit_log.log_leak_warning("https://evil.com", "email", "llm")
        audit_log.log_session("stop")
        assert audit_log.verify_chain() is True

    def test_chain_links_are_sequential(self, audit_log: SecurityAuditLog):
        """Each entry's prev_hash links to the previous entry's hash."""
        audit_log.log_session("start")
        audit_log.log_tool_call("t1", "a1", "llm", "ok")
        audit_log.log_tool_call("t2", "a2", "llm", "ok")

        entries = audit_log.get_recent(10)
        # get_recent returns newest first — reverse for chronological
        entries.reverse()

        assert entries[0]["prev_hash"] == _GENESIS_HASH
        assert entries[1]["prev_hash"] == entries[0]["hash"]
        assert entries[2]["prev_hash"] == entries[1]["hash"]


# ---------------------------------------------------------------------------
# 7. Hash chain tampering detection
# ---------------------------------------------------------------------------


class TestVerifyChainTampered:
    """verify_chain() returns False when the chain has been tampered with."""

    def test_modified_data_detected(self, audit_log: SecurityAuditLog, tmp_path: Path):
        """Altering the data column breaks the hash chain."""
        audit_log.log_session("start")
        audit_log.log_tool_call("open_app", "app=chrome", "llm", "ok")
        audit_log.log_session("stop")

        # Tamper: modify the data of the middle entry directly in SQLite
        db_path = tmp_path / "test_audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE audit_log SET data = ? WHERE id = 2",
            ('{"type": "tool_call", "data": {"tool_name": "EVIL"}, "ts": 0}',),
        )
        conn.commit()
        conn.close()

        # Recreate the log pointing at the same DB
        tampered_log = SecurityAuditLog(db_path=db_path)
        assert tampered_log.verify_chain() is False

    def test_modified_hash_detected(self, audit_log: SecurityAuditLog, tmp_path: Path):
        """Altering an entry's hash breaks the chain for the next entry."""
        audit_log.log_session("start")
        audit_log.log_tool_call("t1", "a1", "llm", "ok")
        audit_log.log_session("stop")

        db_path = tmp_path / "test_audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE audit_log SET hash = ? WHERE id = 1",
            ("deadbeef" * 8,),
        )
        conn.commit()
        conn.close()

        tampered_log = SecurityAuditLog(db_path=db_path)
        assert tampered_log.verify_chain() is False

    def test_deleted_entry_detected(self, audit_log: SecurityAuditLog, tmp_path: Path):
        """Deleting an entry from the middle breaks the chain."""
        audit_log.log_session("start")
        audit_log.log_tool_call("t1", "a1", "llm", "ok")
        audit_log.log_session("stop")

        db_path = tmp_path / "test_audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM audit_log WHERE id = 2")
        conn.commit()
        conn.close()

        tampered_log = SecurityAuditLog(db_path=db_path)
        assert tampered_log.verify_chain() is False


# ---------------------------------------------------------------------------
# 8. Encrypted data column
# ---------------------------------------------------------------------------


class TestEncryptedDataColumn:
    """The data column is encrypted via DPAPI column-level encryption."""

    def test_data_encrypted_on_disk(
        self, tmp_path: Path, dpapi_mock,
    ):
        """With DPAPI, the data column contains encrypted text, not plaintext JSON."""
        from jarvis.platform.secrets import SCHEME_PREFIX

        db_path = tmp_path / "enc_audit.db"
        audit = SecurityAuditLog(db_path=db_path)
        audit.log_tool_call("open_app", "app=chrome", "llm", "success")

        # Read raw from SQLite
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT data FROM audit_log WHERE id = 1"
        ).fetchone()
        conn.close()

        raw_data = row[0]
        # Must be encrypted (dpapi: prefix)
        assert raw_data.startswith(SCHEME_PREFIX), (
            f"Expected encrypted data starting with '{SCHEME_PREFIX}', "
            f"got: {raw_data[:60]}"
        )
        # Plaintext tool name must NOT appear in raw value
        assert "open_app" not in raw_data

    def test_data_decrypted_on_read(
        self, encrypted_audit_log: SecurityAuditLog,
    ):
        """get_recent() returns decrypted, parsed data."""
        encrypted_audit_log.log_tool_call(
            "open_app", "app=chrome", "llm", "success",
        )
        entries = encrypted_audit_log.get_recent(1)
        assert len(entries) == 1
        data = entries[0]["data"]
        assert data["data"]["tool_name"] == "open_app"

    def test_verify_chain_works_with_encryption(
        self, encrypted_audit_log: SecurityAuditLog,
    ):
        """Hash chain verification works correctly with encrypted data."""
        encrypted_audit_log.log_session("start")
        encrypted_audit_log.log_tool_call("t1", "a1", "llm", "ok")
        encrypted_audit_log.log_session("stop")
        assert encrypted_audit_log.verify_chain() is True


# ---------------------------------------------------------------------------
# 9. Recent entries retrieval
# ---------------------------------------------------------------------------


class TestGetRecent:
    """get_recent() retrieves and decodes audit entries."""

    def test_returns_newest_first(self, audit_log: SecurityAuditLog):
        audit_log.log_session("start")
        audit_log.log_tool_call("first", "a", "llm", "ok")
        audit_log.log_tool_call("second", "b", "llm", "ok")

        entries = audit_log.get_recent(2)
        assert len(entries) == 2
        # Newest first
        assert entries[0]["data"]["data"]["tool_name"] == "second"
        assert entries[1]["data"]["data"]["tool_name"] == "first"

    def test_limit_respected(self, audit_log: SecurityAuditLog):
        for i in range(10):
            audit_log.log_tool_call(f"t{i}", f"a{i}", "llm", "ok")
        entries = audit_log.get_recent(3)
        assert len(entries) == 3

    def test_empty_log_returns_empty(self, audit_log: SecurityAuditLog):
        assert audit_log.get_recent() == []

    def test_entry_has_expected_fields(self, audit_log: SecurityAuditLog):
        audit_log.log_session("start")
        entries = audit_log.get_recent(1)
        entry = entries[0]
        assert "id" in entry
        assert "hash" in entry
        assert "prev_hash" in entry
        assert "event_type" in entry
        assert "data" in entry
        assert "timestamp" in entry
        assert isinstance(entry["timestamp"], float)
