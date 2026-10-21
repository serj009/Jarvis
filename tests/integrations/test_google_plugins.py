"""Tests for the Google-suite plugins (Layer A: calendar + contacts).

These run WITHOUT the Google SDK installed: discovery, metadata, tool
schemas, voice patterns, dependency-gating, and the shared-token connect
logic must all work with packages absent. Network paths are not exercised
here (they require the SDK + a live token)."""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

from jarvis.integrations.plugins.google_calendar import GoogleCalendarIntegration
from jarvis.integrations.plugins.google_contacts import (
    GoogleContactsIntegration,
    _next_occurrence,
)
from jarvis.integrations.registry import IntegrationRegistry


class _FakeAuth:
    """Minimal IntegrationAuth stand-in with an in-memory token bucket."""
    def __init__(self, tokens=None):
        self._tokens = tokens

    def get_tokens(self, key):
        return self._tokens


# ── discovery works without the SDK ─────────────────────────────────

class TestDiscovery:
    def test_both_google_plugins_discovered(self):
        reg = IntegrationRegistry()
        names = {p.name for p in reg.discover_plugins()}
        assert "google_calendar" in names
        assert "google_contacts" in names

    def test_underscore_common_not_a_plugin(self):
        reg = IntegrationRegistry()
        names = {p.name for p in reg.discover_plugins()}
        # _google_common must not surface as a plugin.
        assert not any("common" in n for n in names)


# ── metadata + schemas (no SDK) ─────────────────────────────────────

class TestMetadata:
    def test_calendar_metadata(self):
        c = GoogleCalendarIntegration()
        assert c.auth_type == "oauth2"
        assert "google-api-python-client" in c.required_packages
        assert c.enabled is False and c.connected is False

    def test_calendar_tools_shapes(self):
        c = GoogleCalendarIntegration()
        names = {t["function"]["name"] for t in c.get_tools()}
        assert names == {
            "calendar_today", "calendar_week",
            "calendar_create", "calendar_delete",
        }

    def test_contacts_tools_shapes(self):
        c = GoogleContactsIntegration()
        names = {t["function"]["name"] for t in c.get_tools()}
        assert names == {"contacts_find", "contacts_birthdays"}

    def test_voice_patterns_compile(self):
        for plugin in (GoogleCalendarIntegration(), GoogleContactsIntegration()):
            for regex, tool, prio in plugin.get_voice_patterns():
                re.compile(regex)  # must not raise
                assert isinstance(prio, int)
                assert tool in {t["function"]["name"] for t in plugin.get_tools()}


# ── dependency gating ───────────────────────────────────────────────

class TestDependencyGate:
    def test_enable_blocked_without_sdk(self):
        """With the Google SDK absent, enabling must be refused."""
        import importlib.util
        reg = IntegrationRegistry()
        reg.discover_plugins()
        cal = reg.get("google_calendar")
        sdk_present = importlib.util.find_spec("googleapiclient") is not None
        result = reg.enable("google_calendar")
        if sdk_present:
            assert result is True
        else:
            assert result is False
            assert "google-api-python-client" in cal.missing_packages()


# ── shared-token connect logic ──────────────────────────────────────

class TestSharedToken:
    async def test_connect_false_without_token(self):
        c = GoogleCalendarIntegration()
        c.auth = _FakeAuth(tokens=None)
        assert await c.connect() is False
        assert c.connected is False

    async def test_connect_true_with_shared_token(self):
        c = GoogleCalendarIntegration()
        c.auth = _FakeAuth(tokens={"access_token": "abc"})
        assert await c.connect() is True
        assert c.connected is True

    async def test_both_plugins_share_one_token(self):
        shared = _FakeAuth(tokens={"access_token": "xyz"})
        cal = GoogleCalendarIntegration(); cal.auth = shared
        con = GoogleContactsIntegration(); con.auth = shared
        assert await cal.connect() is True
        assert await con.connect() is True  # same token authorises both

    async def test_health_check_reflects_token(self):
        c = GoogleContactsIntegration()
        c.auth = _FakeAuth(tokens=None)
        assert await c.health_check() is False
        c.auth = _FakeAuth(tokens={"access_token": "t"})
        assert await c.health_check() is True


# ── proactive data guards ───────────────────────────────────────────

class TestProactiveGuards:
    def test_calendar_proactive_none_when_disconnected(self):
        c = GoogleCalendarIntegration()
        c.connected = False
        assert c.get_proactive_data() is None

    def test_contacts_proactive_none_when_disconnected(self):
        c = GoogleContactsIntegration()
        c.connected = False
        assert c.get_proactive_data() is None


# ── execute without connection → friendly message, no crash ─────────

class TestExecuteGuards:
    async def test_calendar_execute_without_token(self):
        c = GoogleCalendarIntegration()
        c.auth = _FakeAuth(tokens=None)
        out = await c.execute_tool("calendar_today", {})
        assert "not connected" in out.lower()

    async def test_contacts_execute_without_token(self):
        c = GoogleContactsIntegration()
        c.auth = _FakeAuth(tokens=None)
        out = await c.execute_tool("contacts_find", {"name": "x"})
        assert "not connected" in out.lower()


# ── birthday date helper ────────────────────────────────────────────

class TestNextOccurrence:
    def test_future_this_year(self):
        today = date(2026, 10, 21)
        assert _next_occurrence(today, 12, 25) == date(2026, 12, 25)

    def test_past_rolls_next_year(self):
        today = date(2026, 10, 21)
        assert _next_occurrence(today, 1, 5) == date(2027, 1, 5)

    def test_today_is_zero_days(self):
        today = date(2026, 10, 21)
        assert _next_occurrence(today, 10, 21) == date(2026, 10, 21)

    def test_feb29_non_leap_falls_back(self):
        today = date(2026, 1, 1)
        # 2026 is not a leap year → Feb 29 clamps to Feb 28.
        assert _next_occurrence(today, 2, 29) == date(2026, 2, 28)
