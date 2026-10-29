"""Tests for 13 integration plugins (Layer C, Batch 3+4).

Runs WITHOUT any external SDK installed: discovery, metadata, tool schemas,
voice patterns, dependency-gating, and connect logic must all work with
packages absent. Network paths are not exercised here.
"""

from __future__ import annotations

import re

import pytest

from jarvis.integrations.plugins.ci_cd import CiCdIntegration
from jarvis.integrations.plugins.currency_crypto import CurrencyCryptoIntegration
from jarvis.integrations.plugins.discord_bot import DiscordIntegration
from jarvis.integrations.plugins.docker_local import DockerIntegration

# ── Lazy imports of all plugins ─────────────────────────────────────
from jarvis.integrations.plugins.github import GitHubIntegration
from jarvis.integrations.plugins.monobank import MonobankIntegration
from jarvis.integrations.plugins.news import NewsIntegration
from jarvis.integrations.plugins.privatbank import PrivatBankIntegration
from jarvis.integrations.plugins.spotify import SpotifyIntegration
from jarvis.integrations.plugins.translation import TranslationIntegration
from jarvis.integrations.plugins.weather_extended import WeatherExtendedIntegration
from jarvis.integrations.plugins.whatsapp import WhatsAppIntegration
from jarvis.integrations.plugins.youtube import YouTubeIntegration
from jarvis.integrations.registry import IntegrationRegistry


class _FakeAuth:
    """Minimal IntegrationAuth stand-in."""
    def __init__(self, api_key=None, tokens=None):
        self._api_key = api_key
        self._tokens = tokens

    def get_api_key(self, name):
        return self._api_key

    def get_tokens(self, name):
        return self._tokens


# Names the registry must discover
_EXPECTED_NAMES = {
    "github", "github_actions", "docker", "spotify", "youtube", "whatsapp",
    "monobank", "privatbank", "currency", "news", "weather_extended",
    "translation", "discord",
}


# ─── Discovery ───────────────────────────────────────────────────────

class TestDiscoveryAll:
    def test_all_13_discovered(self):
        reg = IntegrationRegistry()
        found = {p.name for p in reg.discover_plugins()}
        for name in _EXPECTED_NAMES:
            assert name in found, f"{name} not discovered"

    def test_all_disabled_by_default(self):
        reg = IntegrationRegistry()
        reg.discover_plugins()
        for name in _EXPECTED_NAMES:
            plugin = reg.get(name)
            assert plugin is not None, f"{name} not found"
            assert plugin.enabled is False, f"{name} should be disabled"
            assert plugin.connected is False, f"{name} should be disconnected"


# ─── Metadata + Schemas (parametrized) ──────────────────────────────

_PLUGIN_CLASSES = [
    (GitHubIntegration, "github", "api_key", ["PyGithub"]),
    (CiCdIntegration, "github_actions", "api_key", ["PyGithub"]),
    (DockerIntegration, "docker", "none", ["docker"]),
    (SpotifyIntegration, "spotify", "oauth2", ["spotipy"]),
    (YouTubeIntegration, "youtube", "api_key", ["google-api-python-client"]),
    (WhatsAppIntegration, "whatsapp", "none", []),
    (MonobankIntegration, "monobank", "api_key", []),
    (PrivatBankIntegration, "privatbank", "none", []),
    (CurrencyCryptoIntegration, "currency", "api_key", []),
    (NewsIntegration, "news", "api_key", []),
    (WeatherExtendedIntegration, "weather_extended", "api_key", []),
    (TranslationIntegration, "translation", "api_key", []),
    (DiscordIntegration, "discord", "api_key", ["discord.py"]),
]


class TestMetadataAll:
    @pytest.mark.parametrize(
        "cls,expected_name,expected_auth,expected_pkgs",
        _PLUGIN_CLASSES,
        ids=[c[1] for c in _PLUGIN_CLASSES],
    )
    def test_metadata(self, cls, expected_name, expected_auth, expected_pkgs):
        inst = cls()
        assert inst.name == expected_name
        assert inst.auth_type == expected_auth
        assert inst.required_packages == expected_pkgs
        assert inst.enabled is False
        assert inst.connected is False
        assert inst.display_name  # non-empty
        assert inst.category  # non-empty

    @pytest.mark.parametrize(
        "cls,expected_name,expected_auth,expected_pkgs",
        _PLUGIN_CLASSES,
        ids=[c[1] for c in _PLUGIN_CLASSES],
    )
    def test_tools_well_formed(self, cls, expected_name, expected_auth, expected_pkgs):
        inst = cls()
        tools = inst.get_tools()
        assert len(tools) >= 1
        names = set()
        for tool in tools:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params
            names.add(fn["name"])
        # All names unique
        assert len(names) == len(tools)

    @pytest.mark.parametrize(
        "cls,expected_name,expected_auth,expected_pkgs",
        _PLUGIN_CLASSES,
        ids=[c[1] for c in _PLUGIN_CLASSES],
    )
    def test_voice_patterns_compile(self, cls, expected_name, expected_auth, expected_pkgs):
        inst = cls()
        tool_names = {t["function"]["name"] for t in inst.get_tools()}
        patterns = inst.get_voice_patterns()
        assert len(patterns) >= 2, f"{expected_name} should have >=2 voice patterns"
        for regex, tool, prio in patterns:
            re.compile(regex)  # must not raise
            assert isinstance(prio, int)
            assert regex.startswith("^")
            # patterns should be lowercase (normalized transcription)
            assert regex == regex.lower(), f"Pattern not lowercase: {regex}"
            assert tool in tool_names, f"Tool {tool} not in {tool_names}"


# ─── Dependency gating ──────────────────────────────────────────────

class TestDepGating:
    @pytest.mark.parametrize(
        "name,pkgs",
        [("github", ["PyGithub"]), ("github_actions", ["PyGithub"]),
         ("docker", ["docker"]), ("spotify", ["spotipy"]),
         ("discord", ["discord.py"])],
        ids=["github", "github_actions", "docker", "spotify", "discord"],
    )
    def test_enable_blocked_without_sdk(self, name, pkgs):
        import importlib.util
        reg = IntegrationRegistry()
        reg.discover_plugins()
        plugin = reg.get(name)
        assert plugin is not None
        # Map package name to module name for spec check
        special = {
            "PyGithub": "github", "discord.py": "discord",
            "spotipy": "spotipy", "docker": "docker",
        }
        module = special.get(pkgs[0], pkgs[0].replace("-", "_"))
        sdk_present = importlib.util.find_spec(module) is not None
        result = reg.enable(name)
        if sdk_present:
            assert result is True
        else:
            assert result is False

    @pytest.mark.parametrize(
        "name",
        ["whatsapp", "monobank", "privatbank", "currency", "news",
         "weather_extended", "translation"],
        ids=["whatsapp", "monobank", "privatbank", "currency", "news",
             "weather_extended", "translation"],
    )
    def test_no_dep_plugins_enable_freely(self, name):
        """Plugins with empty required_packages should enable without issues."""
        reg = IntegrationRegistry()
        reg.discover_plugins()
        result = reg.enable(name)
        assert result is True


# ─── API key connect logic ──────────────────────────────────────────

_API_KEY_PLUGINS = [
    GitHubIntegration, CiCdIntegration, MonobankIntegration,
    NewsIntegration, WeatherExtendedIntegration, TranslationIntegration,
    DiscordIntegration, YouTubeIntegration,
]


class TestApiKeyConnect:
    @pytest.mark.parametrize("cls", _API_KEY_PLUGINS,
                             ids=[c.name for c in (c() for c in _API_KEY_PLUGINS)])
    async def test_connect_false_without_key(self, cls):
        inst = cls()
        inst.auth = _FakeAuth(api_key=None)
        assert await inst.connect() is False
        assert inst.connected is False

    @pytest.mark.parametrize("cls", _API_KEY_PLUGINS,
                             ids=[c.name for c in (c() for c in _API_KEY_PLUGINS)])
    async def test_connect_true_with_key(self, cls):
        inst = cls()
        inst.auth = _FakeAuth(api_key="test-key-123")
        assert await inst.connect() is True
        assert inst.connected is True

    @pytest.mark.parametrize("cls", _API_KEY_PLUGINS,
                             ids=[c.name for c in (c() for c in _API_KEY_PLUGINS)])
    async def test_health_check_reflects_key(self, cls):
        inst = cls()
        inst.auth = _FakeAuth(api_key=None)
        assert await inst.health_check() is False
        inst.auth = _FakeAuth(api_key="test-key-123")
        assert await inst.health_check() is True


# ─── No-auth plugins connect ────────────────────────────────────────

class TestNoAuthConnect:
    async def test_privatbank_always_connects(self):
        p = PrivatBankIntegration()
        assert await p.connect() is True
        assert p.connected is True

    async def test_currency_always_connects(self):
        c = CurrencyCryptoIntegration()
        assert await c.connect() is True
        assert c.connected is True


# ─── Proactive guard ────────────────────────────────────────────────

class TestProactiveGuard:
    def test_github_proactive_none_when_disconnected(self):
        g = GitHubIntegration()
        g.connected = False
        assert g.get_proactive_data() is None

    def test_youtube_proactive_none_when_disconnected(self):
        y = YouTubeIntegration()
        y.connected = False
        assert y.get_proactive_data() is None

    @pytest.mark.parametrize(
        "cls",
        [PrivatBankIntegration, CiCdIntegration, DockerIntegration,
         WhatsAppIntegration, MonobankIntegration, NewsIntegration,
         WeatherExtendedIntegration, TranslationIntegration, DiscordIntegration],
        ids=["privatbank", "ci_cd", "docker", "whatsapp", "monobank",
             "news", "weather_ext", "translation", "discord"],
    )
    def test_default_proactive_is_none(self, cls):
        inst = cls()
        assert inst.get_proactive_data() is None


# ─── Execute without connection → friendly message ──────────────────

class TestExecuteGuards:
    @pytest.mark.parametrize(
        "cls,tool",
        [(GitHubIntegration, "github_repos"),
         (CiCdIntegration, "actions_status"),
         (MonobankIntegration, "mono_balance"),
         (NewsIntegration, "news_top"),
         (WeatherExtendedIntegration, "weather_week"),
         (TranslationIntegration, "translate_text"),
         (DiscordIntegration, "discord_send"),
         (YouTubeIntegration, "youtube_search")],
        ids=["github", "ci_cd", "monobank", "news", "weather_ext",
             "translation", "discord", "youtube"],
    )
    async def test_execute_without_token_returns_message(self, cls, tool):
        inst = cls()
        inst.auth = _FakeAuth(api_key=None)
        out = await inst.execute_tool(tool, {})
        assert "not connected" in out.lower()

    async def test_docker_execute_without_client(self):
        d = DockerIntegration()
        out = await d.execute_tool("docker_ps", {})
        assert "not connected" in out.lower()

    async def test_whatsapp_execute_without_page(self):
        w = WhatsAppIntegration()
        out = await w.execute_tool("whatsapp_send", {"contact": "x", "message": "y"})
        assert "not connected" in out.lower()

    async def test_spotify_execute_without_sp(self):
        s = SpotifyIntegration()
        out = await s.execute_tool("spotify_play", {"query": "test"})
        assert "not connected" in out.lower()
