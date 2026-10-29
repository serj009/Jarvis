"""Tests for the online-integration plugin framework (Layer 1)."""

from __future__ import annotations

import pytest

from jarvis.integrations.base import BaseIntegration, _pip_to_module
from jarvis.integrations.registry import IntegrationRegistry

# ── discovery ───────────────────────────────────────────────────────

class TestDiscovery:
    def test_discovers_example_plugin(self):
        reg = IntegrationRegistry()
        plugins = reg.discover_plugins()
        names = {p.name for p in plugins}
        assert "example" in names

    def test_example_is_disabled_by_default(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        ex = reg.get("example")
        assert ex is not None and ex.enabled is False

    def test_enabled_names_from_config_mark_enabled(self):
        reg = IntegrationRegistry(enabled_names=["example"])
        reg.discover_plugins()
        assert reg.get("example").enabled is True

    def test_discovery_is_idempotent(self):
        reg = IntegrationRegistry()
        a = {p.name for p in reg.discover_plugins()}
        b = {p.name for p in reg.discover_plugins()}
        assert a == b


# ── enable / disable ────────────────────────────────────────────────

class TestEnableDisable:
    def test_enable_disable_roundtrip(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        assert reg.enable("example") is True
        assert reg.get("example").enabled is True
        assert "example" in reg.enabled_names
        assert reg.disable("example") is True
        assert reg.get("example").enabled is False
        assert "example" not in reg.enabled_names

    def test_enable_unknown_returns_false(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        assert reg.enable("nope") is False

    def test_enable_blocked_by_missing_packages(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        ex = reg.get("example")
        ex.required_packages = ["definitely-not-installed-xyz"]
        assert reg.enable("example") is False
        assert ex.enabled is False
        ex.required_packages = []  # restore

    def test_get_enabled_filters(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        assert reg.get_enabled() == []
        reg.enable("example")
        assert [p.name for p in reg.get_enabled()] == ["example"]


# ── collection ──────────────────────────────────────────────────────

class TestCollection:
    def test_tools_only_from_enabled(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        assert reg.get_all_tools() == []  # nothing enabled
        reg.enable("example")
        tools = reg.get_all_tools()
        names = {t["function"]["name"] for t in tools}
        assert "example_ping" in names

    def test_voice_patterns_only_from_enabled(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        assert reg.get_all_voice_patterns() == []
        reg.enable("example")
        pats = reg.get_all_voice_patterns()
        assert any(p[1] == "example_ping" for p in pats)

    def test_proactive_data_aggregation(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        reg.enable("example")
        # Example returns None → aggregated to empty list.
        assert reg.get_all_proactive_data() == []

    async def test_execute_tool_routes_to_owner(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        reg.enable("example")
        result = await reg.execute_tool("example_ping", {})
        assert result == "pong, sir."

    async def test_execute_tool_unknown_returns_none(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        reg.enable("example")
        assert await reg.execute_tool("nonexistent", {}) is None

    async def test_execute_tool_disabled_returns_none(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        # not enabled → no routing
        assert await reg.execute_tool("example_ping", {}) is None


# ── example plugin lifecycle ────────────────────────────────────────

class TestExamplePlugin:
    async def test_connect_health_disconnect(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        ex = reg.get("example")
        assert await ex.health_check() is False
        assert await ex.connect() is True
        assert ex.connected is True
        assert await ex.health_check() is True
        await ex.disconnect()
        assert ex.connected is False


# ── base helpers ────────────────────────────────────────────────────

class TestBaseHelpers:
    def test_missing_packages_detects_absent(self):
        class _P(BaseIntegration):
            name = "p"
            required_packages = ["definitely-not-installed-xyz"]
            async def connect(self): return True
            async def disconnect(self): return None
            async def health_check(self): return True
            def get_tools(self): return []
            async def execute_tool(self, n, a): return ""
            def get_voice_patterns(self): return []

        assert _P().missing_packages() == ["definitely-not-installed-xyz"]

    def test_missing_packages_empty_when_present(self):
        class _P(BaseIntegration):
            name = "p"
            required_packages = ["os"]  # stdlib, always importable
            async def connect(self): return True
            async def disconnect(self): return None
            async def health_check(self): return True
            def get_tools(self): return []
            async def execute_tool(self, n, a): return ""
            def get_voice_patterns(self): return []

        assert _P().missing_packages() == []

    def test_pip_to_module_specialcases(self):
        assert _pip_to_module("google-api-python-client") == "googleapiclient"
        assert _pip_to_module("python-telegram-bot") == "telegram"
        assert _pip_to_module("some-pkg") == "some_pkg"


# ── auth (DPAPI store, tmp path) ────────────────────────────────────

class TestIntegrationAuth:
    def _auth(self, tmp_path):
        from jarvis.integrations.auth import IntegrationAuth
        return IntegrationAuth(path=tmp_path / "creds.json")

    def test_api_key_roundtrip(self, tmp_path):
        auth = self._auth(tmp_path)
        assert auth.get_api_key("svc") is None
        auth.store_api_key("svc", "secret123")
        assert auth.get_api_key("svc") == "secret123"
        assert auth.has_credentials("svc") is True

    def test_tokens_roundtrip(self, tmp_path):
        auth = self._auth(tmp_path)
        auth.store_tokens("svc", {"access_token": "a", "refresh_token": "r"})
        tok = auth.get_tokens("svc")
        assert tok["access_token"] == "a" and tok["refresh_token"] == "r"

    def test_delete_credentials(self, tmp_path):
        auth = self._auth(tmp_path)
        auth.store_api_key("svc", "x")
        assert auth.delete_credentials("svc") is True
        assert auth.get_api_key("svc") is None
        assert auth.delete_credentials("svc") is False

    def test_build_auth_url(self, tmp_path):
        auth = self._auth(tmp_path)
        url = auth.build_auth_url(
            "https://accounts.google.com/o/oauth2/v2/auth",
            client_id="cid", redirect_uri="http://localhost:8765",
            scopes=["email", "profile"],
        )
        assert url.startswith("https://accounts.google.com")
        assert "client_id=cid" in url
        assert "scope=email+profile" in url

    def test_credentials_encrypted_at_rest(self, tmp_path):
        """The raw file must not contain the plaintext secret (DPAPI).

        Skipped where DPAPI is unavailable (non-Windows), since the store
        documents a plaintext fallback there for contributor dev machines.
        """
        from jarvis.platform.secrets import dpapi_available

        if not dpapi_available():
            pytest.skip("DPAPI unavailable; encrypted-at-rest is Windows-only")
        auth = self._auth(tmp_path)
        auth.store_api_key("svc", "PLAINTEXT_SECRET_VALUE")
        raw = (tmp_path / "creds.json").read_bytes()
        assert b"PLAINTEXT_SECRET_VALUE" not in raw


# ── config + migration ──────────────────────────────────────────────

def test_integrations_config_default():
    from jarvis.core.config import JarvisConfig
    cfg = JarvisConfig()
    assert cfg.integrations.enabled == []


def test_migration_v38_to_v39():
    from jarvis.core.migrations import MIGRATIONS, _migrate_v38_to_v39
    data = _migrate_v38_to_v39({"schema_version": 38})
    assert data["schema_version"] == 39
    assert data["integrations"]["enabled"] == []
    assert 38 in MIGRATIONS
