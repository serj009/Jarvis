"""Online Integrations settings tab.

Lists every discovered integration plugin with a toggle, status dot, and
(for api_key plugins) a masked key field + Save. Enabling is gated on the
plugin's pip dependencies being present. Everything is OFF by default — the
tab is where the user explicitly opts a service online, with a clear
"this sends data to {service}" warning.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, ToggleSwitch

log = logging.getLogger(__name__)

_STATUS = {
    "disabled": "\U0001f534 Disabled",
    "enabled": "\U0001f7e1 Enabled (not connected)",
    "connected": "\U0001f7e2 Connected",
}


class IntegrationsTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        registry=None,
        auth=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_change = on_change

        # Build a registry/auth if the composition root didn't pass live
        # ones (keeps the tab self-sufficient in tests).
        if registry is None:
            from jarvis.integrations.registry import IntegrationRegistry

            registry = IntegrationRegistry(
                enabled_names=list(config.integrations.enabled)
            )
            registry.discover_plugins()
        if auth is None:
            from jarvis.integrations.auth import IntegrationAuth

            auth = IntegrationAuth()
        self._registry = registry
        self._auth = auth
        self._rows: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        title = QLabel("Online Integrations")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Connect online services (Gmail, Calendar, and more). Everything "
            "here is OFF by default \u2014 Jarvis stays fully local until you "
            "enable an integration. Enabling one sends data to that service "
            "over the internet; credentials are stored encrypted on this PC."
        )
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        plugins = sorted(self._registry.all(), key=lambda p: p.display_name)
        if not plugins:
            empty = QLabel("No integrations are installed yet.")
            empty.setObjectName("settingHint")
            root.addWidget(empty)
        for plugin in plugins:
            root.addWidget(self._build_card(plugin))
        root.addStretch()

    # -- per-plugin card -------------------------------------------------

    def _build_card(self, plugin) -> SectionCard:
        card = SectionCard()
        card.add_header(plugin.icon or "\U0001f50c", plugin.display_name)

        toggle = ToggleSwitch(checked=plugin.enabled)
        card.add_row(plugin.description or "Online integration", "", toggle)

        status = QLabel(self._status_text(plugin))
        status.setObjectName("settingHint")
        card.add_row("Status", "", status)

        row: dict = {"toggle": toggle, "status": status, "key_field": None}

        # api_key plugins get a masked field + Save.
        if plugin.auth_type == "api_key":
            field = QLineEdit(self._auth.get_api_key(plugin.name) or "")
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText("API key \u2014 stored encrypted (DPAPI)")
            save_btn = QPushButton("Save key")
            save_btn.clicked.connect(lambda _=False, p=plugin: self._save_key(p))
            card.add_row("API key", "", field)
            card.add_row("", "", save_btn, last=True)
            row["key_field"] = field

        toggle.toggled.connect(lambda val, p=plugin: self._on_toggle(p, val))
        self._rows[plugin.name] = row
        return card

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _status_text(plugin) -> str:
        if not plugin.enabled:
            return _STATUS["disabled"]
        return _STATUS["connected"] if plugin.connected else _STATUS["enabled"]

    def _refresh_status(self, plugin) -> None:
        row = self._rows.get(plugin.name)
        if row:
            row["status"].setText(self._status_text(plugin))

    def _on_toggle(self, plugin, value: bool) -> None:
        if value:
            missing = plugin.missing_packages()
            if missing:
                # Revert the toggle; report the missing deps.
                row = self._rows[plugin.name]
                row["toggle"].blockSignals(True)
                row["toggle"].setChecked(False)
                row["toggle"].blockSignals(False)
                row["status"].setText(
                    f"\U0001f534 Needs: pip install {' '.join(missing)}"
                )
                return
            self._registry.enable(plugin.name)
        else:
            self._registry.disable(plugin.name)
        self._config.integrations.enabled = self._registry.enabled_names
        self._refresh_status(plugin)
        self._persist()

    def _save_key(self, plugin) -> None:
        row = self._rows.get(plugin.name)
        if not row or row["key_field"] is None:
            return
        key = row["key_field"].text().strip()
        if key:
            self._auth.store_api_key(plugin.name, key)
            row["status"].setText("\U0001f7e1 Key saved \u2014 toggle on to enable")
        self._on_change()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("Failed to save integrations config")
        self._on_change()
