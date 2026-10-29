"""Tests for Tier 3 plugins (15 integrations).

Runs WITHOUT external SDKs installed: discovery, metadata, tool schemas,
voice patterns, and api_key connect logic must all work with packages
absent. Network paths are not exercised.
"""

from __future__ import annotations

import re

import pytest

from jarvis.integrations.plugins.banking_generic import BankingIntegration
from jarvis.integrations.plugins.dropbox import DropboxIntegration
from jarvis.integrations.plugins.gitlab import GitLabIntegration
from jarvis.integrations.plugins.google_maps import GoogleMapsIntegration
from jarvis.integrations.plugins.home_assistant import HomeAssistantIntegration
from jarvis.integrations.plugins.hue import PhilipsHueIntegration
from jarvis.integrations.plugins.mqtt import MqttIntegration

# -- imports for every Tier 3 plugin ------------------------------------
from jarvis.integrations.plugins.notion import NotionIntegration
from jarvis.integrations.plugins.onedrive import OneDriveIntegration
from jarvis.integrations.plugins.ride_hailing import RideHailingIntegration
from jarvis.integrations.plugins.slack import SlackIntegration
from jarvis.integrations.plugins.todoist import TodoistIntegration
from jarvis.integrations.plugins.trello import TrelloIntegration
from jarvis.integrations.plugins.twitch import TwitchIntegration
from jarvis.integrations.plugins.wikipedia import WikipediaIntegration
from jarvis.integrations.registry import IntegrationRegistry


class _FakeAuth:
    """Minimal IntegrationAuth stand-in with an in-memory store."""
    def __init__(self, api_key=None, fields=None):
        self._api_key = api_key
        self._fields = fields or {}

    def get_api_key(self, name):
        return self._api_key

    def get_field(self, name, field):
        return self._fields.get(field)


# ── Mapping: (plugin_class, name, expected_tools, auth_type, has_proactive) ──

TIER3_PLUGINS = [
    (NotionIntegration, "notion",
     {"notion_search", "notion_read", "notion_create", "notion_add_to_db"},
     "api_key", False),
    (TrelloIntegration, "trello",
     {"trello_boards", "trello_cards", "trello_create_card", "trello_move_card"},
     "api_key", False),
    (TodoistIntegration, "todoist",
     {"todoist_today", "todoist_add", "todoist_complete", "todoist_projects"},
     "api_key", True),
    (SlackIntegration, "slack",
     {"slack_send", "slack_read", "slack_channels", "slack_status"},
     "api_key", False),
    (DropboxIntegration, "dropbox",
     {"dropbox_search", "dropbox_upload", "dropbox_download", "dropbox_list"},
     "oauth2", False),
    (OneDriveIntegration, "onedrive",
     {"onedrive_search", "onedrive_upload", "onedrive_download", "onedrive_list"},
     "oauth2", False),
    (GitLabIntegration, "gitlab",
     {"gitlab_projects", "gitlab_issues", "gitlab_create_issue",
      "gitlab_mrs", "gitlab_pipelines"},
     "api_key", False),
    (TwitchIntegration, "twitch",
     {"twitch_live", "twitch_search", "twitch_open"},
     "oauth2", True),
    (WikipediaIntegration, "wikipedia",
     {"wiki_search", "wiki_summary", "wiki_random"},
     "none", False),
    (HomeAssistantIntegration, "home_assistant",
     {"ha_devices", "ha_turn_on", "ha_turn_off", "ha_set", "ha_status"},
     "api_key", False),
    (PhilipsHueIntegration, "philips_hue",
     {"hue_on", "hue_off", "hue_color", "hue_brightness", "hue_scene"},
     "api_key", False),
    (MqttIntegration, "mqtt",
     {"mqtt_publish", "mqtt_subscribe", "mqtt_status"},
     "api_key", False),
    (BankingIntegration, "banking",
     {"bank_balance", "bank_transactions", "bank_rates"},
     "api_key", False),
    (GoogleMapsIntegration, "google_maps",
     {"maps_route", "maps_traffic", "maps_nearby"},
     "api_key", True),
    (RideHailingIntegration, "ride_hailing",
     {"ride_estimate", "ride_request", "ride_status"},
     "api_key", False),
]


# ── Discovery ──────────────────────────────────────────────────────────

class TestDiscovery:
    """All 15 Tier 3 plugins must be discovered by the registry."""

    def test_all_tier3_discovered(self):
        reg = IntegrationRegistry()
        discovered = {p.name for p in reg.discover_plugins()}
        for _, name, *_ in TIER3_PLUGINS:
            assert name in discovered, f"{name} not discovered"


# ── Metadata + Tool Schemas ────────────────────────────────────────────

class TestMetadata:
    """Metadata, auth_type, disabled-by-default, and tool schema shapes."""

    @pytest.mark.parametrize(
        "cls,name,expected_tools,auth_type,_",
        TIER3_PLUGINS,
        ids=[t[1] for t in TIER3_PLUGINS],
    )
    def test_metadata(self, cls, name, expected_tools, auth_type, _):
        inst = cls()
        assert inst.name == name
        assert inst.auth_type == auth_type
        assert inst.enabled is False
        assert inst.connected is False

    @pytest.mark.parametrize(
        "cls,name,expected_tools,_a,_b",
        TIER3_PLUGINS,
        ids=[t[1] for t in TIER3_PLUGINS],
    )
    def test_tool_schemas(self, cls, name, expected_tools, _a, _b):
        inst = cls()
        tools = inst.get_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == expected_tools, f"{name} tools mismatch: {names}"
        for t in tools:
            fn = t["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"


# ── Voice Patterns ─────────────────────────────────────────────────────

class TestVoicePatterns:
    """All patterns compile, are lowercase, start with ^, and reference valid tools."""

    @pytest.mark.parametrize(
        "cls,name,expected_tools,_a,_b",
        TIER3_PLUGINS,
        ids=[t[1] for t in TIER3_PLUGINS],
    )
    def test_patterns_valid(self, cls, name, expected_tools, _a, _b):
        inst = cls()
        tool_names = {t["function"]["name"] for t in inst.get_tools()}
        for regex, tool, prio in inst.get_voice_patterns():
            re.compile(regex)  # must not raise
            assert isinstance(prio, int)
            assert regex.startswith("^"), f"{name}: pattern missing ^: {regex}"
            assert regex == regex.lower(), f"{name}: pattern not lowercase: {regex}"
            assert tool in tool_names, f"{name}: pattern tool {tool} not in tools"


# ── Connect / Disconnect / Health ──────────────────────────────────────

class TestConnect:
    """api_key plugins: no key → connected=False, with key → True.
    Wikipedia (auth_type=none) always connects."""

    @pytest.mark.parametrize(
        "cls,name,_t,auth_type,_p",
        [p for p in TIER3_PLUGINS if p[3] != "none"],
        ids=[p[1] for p in TIER3_PLUGINS if p[3] != "none"],
    )
    async def test_connect_false_without_key(self, cls, name, _t, auth_type, _p):
        inst = cls()
        inst.auth = _FakeAuth(api_key=None)
        assert await inst.connect() is False
        assert inst.connected is False

    @pytest.mark.parametrize(
        "cls,name,_t,auth_type,_p",
        [p for p in TIER3_PLUGINS if p[3] != "none"],
        ids=[p[1] for p in TIER3_PLUGINS if p[3] != "none"],
    )
    async def test_connect_true_with_key(self, cls, name, _t, auth_type, _p):
        inst = cls()
        inst.auth = _FakeAuth(api_key="test-key-123",
                              fields={"api_token": "test-token",
                                      "access_token": "test-at"})
        result = await inst.connect()
        assert result is True
        assert inst.connected is True

    async def test_wikipedia_always_connects(self):
        w = WikipediaIntegration()
        assert await w.connect() is True
        assert w.connected is True


# ── Execute guards (no crash without connection) ───────────────────────

class TestExecuteGuards:
    """Calling execute_tool without auth returns a friendly message, no crash."""

    @pytest.mark.parametrize(
        "cls,name,expected_tools,auth_type,_p",
        [p for p in TIER3_PLUGINS if p[3] != "none"],
        ids=[p[1] for p in TIER3_PLUGINS if p[3] != "none"],
    )
    async def test_execute_without_token(self, cls, name, expected_tools, auth_type, _p):
        inst = cls()
        inst.auth = _FakeAuth(api_key=None)
        first_tool = sorted(expected_tools)[0]
        result = await inst.execute_tool(first_tool, {})
        assert "not connected" in result.lower()


# ── Proactive guard ───────────────────────────────────────────────────

class TestProactive:
    """Proactive returns None when disconnected."""

    @pytest.mark.parametrize(
        "cls,name,_t,_a,has_proactive",
        [p for p in TIER3_PLUGINS if p[4]],
        ids=[p[1] for p in TIER3_PLUGINS if p[4]],
    )
    def test_proactive_none_when_disconnected(self, cls, name, _t, _a, has_proactive):
        inst = cls()
        inst.connected = False
        assert inst.get_proactive_data() is None

    def test_default_proactive_none(self):
        """Plugins without proactive should return None."""
        for cls, _name, _, _, has_proactive in TIER3_PLUGINS:
            if not has_proactive:
                inst = cls()
                inst.connected = False
                assert inst.get_proactive_data() is None
