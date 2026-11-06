"""IntegrationAuth.get_field: plugins' extra settings (M2-1 finding).

Several plugins called auth.get_field(...) behind hasattr() checks, but
IntegrationAuth had no such method, so settings like Hue's bridge IP were
never read and the hard-coded default was always used.
"""

from __future__ import annotations

from jarvis.integrations.auth import IntegrationAuth


def test_field_roundtrip(tmp_path):
    auth = IntegrationAuth(path=tmp_path / "creds.json")
    assert auth.get_field("hue", "bridge_ip") is None
    auth.store_field("hue", "bridge_ip", "10.0.0.7")
    auth.store_api_key("hue", "key-1")
    again = IntegrationAuth(path=tmp_path / "creds.json")
    assert again.get_field("hue", "bridge_ip") == "10.0.0.7"
    assert again.get_api_key("hue") == "key-1"


def test_hue_uses_the_stored_bridge_ip(tmp_path):
    from jarvis.integrations.plugins.hue import PhilipsHueIntegration

    plugin = PhilipsHueIntegration()
    auth = IntegrationAuth(path=tmp_path / "creds.json")
    auth.store_api_key(plugin.name, "key-1")
    auth.store_field(plugin.name, "bridge_ip", "10.0.0.7")
    plugin.auth = auth
    assert plugin._base() == "http://10.0.0.7/api/key-1"


def test_trello_token_comes_from_the_stored_field(tmp_path):
    from jarvis.integrations.plugins.trello import TrelloIntegration

    plugin = TrelloIntegration()
    auth = IntegrationAuth(path=tmp_path / "creds.json")
    auth.store_api_key(plugin.name, "k")
    auth.store_field(plugin.name, "api_token", "t")
    plugin.auth = auth
    assert plugin._creds() == ("k", "t")
