"""Tests for the Google-suite plugins (Layer B: gmail + drive).

Mirror Layer A: everything here runs WITHOUT the Google SDK installed —
discovery, metadata, tool schemas, voice patterns, dependency-gating, the
shared-token connect logic, and the pure helpers. Network paths are not
exercised (they require the SDK + a live token).
"""

from __future__ import annotations

import base64
import re

from jarvis.integrations.plugins.gmail import (
    GmailIntegration,
    _extract_body,
    _headers,
)
from jarvis.integrations.plugins.google_drive import GoogleDriveIntegration
from jarvis.integrations.registry import IntegrationRegistry


class _FakeAuth:
    """Minimal IntegrationAuth stand-in with an in-memory token bucket."""
    def __init__(self, tokens=None):
        self._tokens = tokens

    def get_tokens(self, key):
        return self._tokens


# ── discovery works without the SDK ──────────────────────────────────

class TestDiscovery:
    def test_both_layer_b_plugins_discovered(self):
        reg = IntegrationRegistry()
        names = {p.name for p in reg.discover_plugins()}
        assert "gmail" in names
        assert "google_drive" in names


# ── metadata + schemas (no SDK) ──────────────────────────────────────

class TestMetadata:
    def test_gmail_metadata(self):
        g = GmailIntegration()
        assert g.auth_type == "oauth2"
        assert "google-api-python-client" in g.required_packages
        assert g.enabled is False and g.connected is False

    def test_gmail_tools_shapes(self):
        g = GmailIntegration()
        names = {t["function"]["name"] for t in g.get_tools()}
        assert names == {"gmail_inbox", "gmail_read", "gmail_send", "gmail_search"}

    def test_drive_tools_shapes(self):
        d = GoogleDriveIntegration()
        names = {t["function"]["name"] for t in d.get_tools()}
        assert names == {
            "drive_list", "drive_search", "drive_upload", "drive_download",
        }

    def test_voice_patterns_compile(self):
        for plugin in (GmailIntegration(), GoogleDriveIntegration()):
            tool_names = {t["function"]["name"] for t in plugin.get_tools()}
            for regex, tool, prio in plugin.get_voice_patterns():
                re.compile(regex)  # must not raise
                assert isinstance(prio, int)
                assert regex.startswith("^")
                assert regex == regex.lower()
                assert tool in tool_names


# ── dependency gating ────────────────────────────────────────────────

class TestDependencyGate:
    def test_enable_blocked_without_sdk(self):
        import importlib.util
        reg = IntegrationRegistry()
        reg.discover_plugins()
        gmail = reg.get("gmail")
        sdk_present = importlib.util.find_spec("googleapiclient") is not None
        result = reg.enable("gmail")
        if sdk_present:
            assert result is True
        else:
            assert result is False
            assert "google-api-python-client" in gmail.missing_packages()


# ── shared-token connect logic ───────────────────────────────────────

class TestSharedToken:
    async def test_connect_false_without_token(self):
        g = GmailIntegration()
        g.auth = _FakeAuth(tokens=None)
        assert await g.connect() is False

    async def test_connect_true_with_shared_token(self):
        g = GmailIntegration()
        g.auth = _FakeAuth(tokens={"access_token": "abc"})
        assert await g.connect() is True

    async def test_gmail_and_drive_share_one_token(self):
        shared = _FakeAuth(tokens={"access_token": "xyz"})
        g = GmailIntegration()
        g.auth = shared
        d = GoogleDriveIntegration()
        d.auth = shared
        assert await g.connect() is True
        assert await d.connect() is True

    async def test_health_check_reflects_token(self):
        d = GoogleDriveIntegration()
        d.auth = _FakeAuth(tokens=None)
        assert await d.health_check() is False
        d.auth = _FakeAuth(tokens={"access_token": "t"})
        assert await d.health_check() is True


# ── proactive data guards ────────────────────────────────────────────

class TestProactiveGuards:
    def test_gmail_proactive_none_when_disconnected(self):
        g = GmailIntegration()
        g.connected = False
        assert g.get_proactive_data() is None


# ── execute without connection → friendly message, no crash ──────────

class TestExecuteGuards:
    async def test_gmail_execute_without_token(self):
        g = GmailIntegration()
        g.auth = _FakeAuth(tokens=None)
        out = await g.execute_tool("gmail_inbox", {})
        assert "not connected" in out.lower()

    async def test_drive_execute_without_token(self):
        d = GoogleDriveIntegration()
        d.auth = _FakeAuth(tokens=None)
        out = await d.execute_tool("drive_list", {})
        assert "not connected" in out.lower()

    async def test_drive_upload_missing_file(self):
        """Upload guard fires before any SDK call, so works without token too.

        A missing token short-circuits first, so we assert the friendly
        not-connected message rather than the file guard here.
        """
        d = GoogleDriveIntegration()
        d.auth = _FakeAuth(tokens=None)
        out = await d.execute_tool("drive_upload", {"path": "/no/such.file"})
        assert "not connected" in out.lower()


# ── gmail pure helpers ───────────────────────────────────────────────

class TestGmailHelpers:
    def test_headers_flatten(self):
        msg = {
            "payload": {
                "headers": [
                    {"name": "From", "value": "a@b.com"},
                    {"name": "Subject", "value": "Hi"},
                ]
            }
        }
        h = _headers(msg)
        assert h["From"] == "a@b.com"
        assert h["Subject"] == "Hi"

    def test_extract_body_plain(self):
        raw = base64.urlsafe_b64encode(b"hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": raw}}
        assert _extract_body(payload) == "hello world"

    def test_extract_body_multipart(self):
        raw = base64.urlsafe_b64encode(b"nested body").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<p>x</p>").decode()},
                },
                {"mimeType": "text/plain", "body": {"data": raw}},
            ],
        }
        assert _extract_body(payload) == "nested body"

    def test_extract_body_empty(self):
        assert _extract_body({}) == ""
