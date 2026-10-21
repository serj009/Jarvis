"""Tests for the Telegram plugin (Layer C).

Runs WITHOUT python-telegram-bot installed: discovery, metadata, tool
schemas, voice patterns, dependency-gating, and the api_key connect logic
must all work with the package absent. Network paths (send/read) are not
exercised here — they need the SDK + a live bot token.
"""

from __future__ import annotations

import re

from jarvis.integrations.plugins.telegram_bot import TelegramIntegration
from jarvis.integrations.registry import IntegrationRegistry


class _FakeAuth:
    """Minimal IntegrationAuth stand-in with an in-memory api_key store."""
    def __init__(self, api_key=None):
        self._api_key = api_key

    def get_api_key(self, name):
        return self._api_key


# ── discovery ────────────────────────────────────────────────────────

class TestDiscovery:
    def test_telegram_discovered(self):
        reg = IntegrationRegistry()
        names = {p.name for p in reg.discover_plugins()}
        assert "telegram" in names


# ── metadata + schemas ───────────────────────────────────────────────

class TestMetadata:
    def test_telegram_metadata(self):
        t = TelegramIntegration()
        assert t.auth_type == "api_key"
        assert t.required_packages == ["python-telegram-bot"]
        assert t.enabled is False and t.connected is False
        # api_key field declared for the Settings form
        assert any(f["name"] == "api_key" for f in t.auth_fields)

    def test_telegram_tools_shapes(self):
        t = TelegramIntegration()
        names = {tool["function"]["name"] for tool in t.get_tools()}
        assert names == {"telegram_send", "telegram_read", "telegram_send_file"}

    def test_voice_patterns_compile(self):
        t = TelegramIntegration()
        tool_names = {tool["function"]["name"] for tool in t.get_tools()}
        for regex, tool, prio in t.get_voice_patterns():
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
        tg = reg.get("telegram")
        sdk_present = importlib.util.find_spec("telegram") is not None
        result = reg.enable("telegram")
        if sdk_present:
            assert result is True
        else:
            assert result is False
            assert "python-telegram-bot" in tg.missing_packages()


# ── api_key connect logic ────────────────────────────────────────────

class TestApiKeyConnect:
    async def test_connect_false_without_key(self):
        t = TelegramIntegration()
        t.auth = _FakeAuth(api_key=None)
        assert await t.connect() is False
        assert t.connected is False

    async def test_connect_true_with_key(self):
        t = TelegramIntegration()
        t.auth = _FakeAuth(api_key="123:abc")
        assert await t.connect() is True
        assert t.connected is True

    async def test_health_check_reflects_key(self):
        t = TelegramIntegration()
        t.auth = _FakeAuth(api_key=None)
        assert await t.health_check() is False
        t.auth = _FakeAuth(api_key="123:abc")
        assert await t.health_check() is True


# ── proactive guard ──────────────────────────────────────────────────

class TestProactiveGuard:
    def test_proactive_none_when_disconnected(self):
        t = TelegramIntegration()
        t.connected = False
        assert t.get_proactive_data() is None


# ── execute without connection → friendly message, no crash ──────────

class TestExecuteGuards:
    async def test_send_without_token(self):
        t = TelegramIntegration()
        t.auth = _FakeAuth(api_key=None)
        out = await t.execute_tool("telegram_send", {"chat_id": "1", "text": "hi"})
        assert "not connected" in out.lower()

    async def test_read_without_token(self):
        t = TelegramIntegration()
        t.auth = _FakeAuth(api_key=None)
        out = await t.execute_tool("telegram_read", {})
        assert "not connected" in out.lower()
