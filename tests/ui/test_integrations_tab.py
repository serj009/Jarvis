"""Tests for the Online Integrations settings tab (Layer 2).

Lives under tests/ui/ so it gets the offscreen qapp fixture from
tests/ui/conftest.py (QT_QPA_PLATFORM=offscreen set before PySide6 import).
"""

from __future__ import annotations

from unittest.mock import patch

from jarvis.core.config import JarvisConfig
from jarvis.integrations.auth import IntegrationAuth
from jarvis.integrations.registry import IntegrationRegistry
from jarvis.ui.settings import SettingsWindow
from jarvis.ui.settings.tabs.integrations import IntegrationsTab


def _build_tab(cfg, auth_path):
    reg = IntegrationRegistry(enabled_names=list(cfg.integrations.enabled))
    reg.discover_plugins()
    auth = IntegrationAuth(path=auth_path)
    changed: list[int] = []
    tab = IntegrationsTab(
        config=cfg, on_change=lambda: changed.append(1),
        registry=reg, auth=auth,
    )
    return tab, reg, auth, changed


class TestIntegrationsTab:
    def test_lists_example(self, qapp, tmp_path):
        cfg = JarvisConfig()
        tab, _reg, _auth, _ch = _build_tab(cfg, tmp_path / "c.json")
        assert "example" in tab._rows

    def test_toggle_enables_and_persists(self, qapp, tmp_path):
        cfg = JarvisConfig()
        tab, reg, _auth, changed = _build_tab(cfg, tmp_path / "c.json")
        tab._on_toggle(reg.get("example"), True)
        assert reg.get("example").enabled is True
        assert cfg.integrations.enabled == ["example"]
        assert changed

    def test_toggle_off_removes_from_config(self, qapp, tmp_path):
        cfg = JarvisConfig()
        cfg.integrations.enabled = ["example"]
        tab, reg, _auth, _ch = _build_tab(cfg, tmp_path / "c.json")
        tab._on_toggle(reg.get("example"), False)
        assert cfg.integrations.enabled == []

    def test_toggle_blocked_by_missing_deps_reverts(self, qapp, tmp_path):
        cfg = JarvisConfig()
        tab, reg, _auth, _ch = _build_tab(cfg, tmp_path / "c.json")
        ex = reg.get("example")
        ex.required_packages = ["definitely-not-installed-xyz"]
        try:
            tab._on_toggle(ex, True)
            assert ex.enabled is False
            assert cfg.integrations.enabled == []
        finally:
            ex.required_packages = []


def test_settings_window_accepts_integration_registry(qapp):
    reg = IntegrationRegistry()
    reg.discover_plugins()
    with patch("jarvis.ui.settings.tabs.about.AboutTab._refresh_status"):
        win = SettingsWindow(
            config=JarvisConfig(), on_change=lambda: None,
            integration_registry=reg,
        )
    try:
        assert "integrations" in win._nav_buttons
    finally:
        win.close()
