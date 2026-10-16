"""Security settings tab: permissions, sandbox, vault."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import Badge, SectionCard, slider_with_label

log = logging.getLogger(__name__)


class SecurityTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_change = on_change

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        title = QLabel("Security")
        title.setObjectName("pageTitle")
        desc = QLabel("Permissions, confirmations, and password vault")
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Permissions card --
        perm_card = SectionCard()
        perm_card.add_header(
            "\U0001f6e1\ufe0f", "Permissions",
        )

        self.confirm_destructive = QCheckBox()
        self.confirm_destructive.setChecked(config.sandbox.confirm_destructive)
        perm_card.add_row(
            "Confirm destructive actions",
            "Ask before deleting files, closing apps, running system commands",
            self.confirm_destructive,
        )

        self.encrypt_memory = QCheckBox()
        self.encrypt_memory.setChecked(config.security.encrypt_memory)
        perm_card.add_row(
            "Encrypt memory at rest",
            "Encrypt stored facts with Windows DPAPI",
            self.encrypt_memory,
        )

        self.encrypt_json = QCheckBox()
        self.encrypt_json.setChecked(config.security.encrypt_json_files)
        perm_card.add_row(
            "Encrypt JSON files",
            "Encrypt profile, bookmarks, aliases files",
            self.encrypt_json,
            last=True,
        )

        root.addWidget(perm_card)

        # -- Sandbox card --
        sandbox_card = SectionCard()
        sandbox_card.add_header(
            "\U0001f4e6", "Workspace Sandbox",
            badge=Badge("Active", "ok"),
        )

        info = QLabel(
            "Tools can only access whitelisted directories. "
            "Edit allowed paths in config.json → sandbox.allowed_paths"
        )
        info.setWordWrap(True)
        info.setObjectName("settingHint")
        sandbox_card.add_widget(info)

        root.addWidget(sandbox_card)

        # -- Vault card --
        vault_card = SectionCard()
        vault_card.add_header(
            "\U0001f512", "Password Vault",
            badge=Badge("Encrypted", "ok"),
        )

        change_pw = QPushButton("Change master password")
        change_pw.setObjectName("btnSmall")
        change_pw.clicked.connect(self._on_change_password)
        vault_card.add_row(
            "Master password",
            "Protects all stored credentials",
            change_pw,
            last=True,
        )

        root.addWidget(vault_card)
        root.addStretch(1)

        # -- Connections --
        self.confirm_destructive.stateChanged.connect(self._on_confirm)
        self.encrypt_memory.stateChanged.connect(self._on_encrypt_memory)
        self.encrypt_json.stateChanged.connect(self._on_encrypt_json)

    def _on_confirm(self) -> None:
        self._config.sandbox.confirm_destructive = self.confirm_destructive.isChecked()
        self._persist()

    def _on_encrypt_memory(self) -> None:
        self._config.security.encrypt_memory = self.encrypt_memory.isChecked()
        self._persist()

    def _on_encrypt_json(self) -> None:
        self._config.security.encrypt_json_files = self.encrypt_json.isChecked()
        self._persist()

    def _on_change_password(self) -> None:
        # The actual password change dialog lives in the vault tool.
        # This button is a placeholder — ideally it opens a dialog.
        log.info("change vault password clicked (dialog not wired yet)")

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from SecurityTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from SecurityTab")
