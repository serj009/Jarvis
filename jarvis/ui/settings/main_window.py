"""SettingsWindow — sidebar navigation + stacked content pages.

Opens from the tray "Settings…" menu item (and the open_settings
hotkey). Non-modal: closing it doesn't quit Jarvis. The composition
root keeps a single instance alive across opens; show() / raise_()
brings it back into focus."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent

from jarvis.ui.settings.tabs import (
    AboutTab,
    AdvancedTab,
    CodingTab,
    CommandsTab,
    GamingTab,
    GeneralTab,
    HotkeysTab,
    ImageGenTab,
    MemoryTab,
    ModelsTab,
    SecurityTab,
    ToolsTab,
    VoiceTab,
)
from jarvis.ui.settings.theme import apply_theme

log = logging.getLogger(__name__)

_DEFAULT_SIZE = (900, 640)

# Navigation items: (page_key, icon_emoji, display_label, section_header_or_None)
_NAV_ITEMS: tuple[tuple[str, str, str, str | None], ...] = (
    ("general",  "\u2699\ufe0f",  "General",       "System"),
    ("voice",    "\U0001f3a4",    "Voice & Audio", None),
    ("models",   "\U0001f9e0",    "AI Models",     None),
    ("tools",    "\U0001f9e9",    "Tools",         "Features"),
    ("coding",   "\U0001f4bb",    "Coding",        None),
    ("memory",   "\U0001f4be",    "Memory",        None),
    ("gaming",   "\U0001f3ae",    "Game Mode",     None),
    ("image_gen","\U0001f3a8",    "Image Gen",     None),
    ("security", "\U0001f6e1\ufe0f", "Security",   None),
    ("commands", "\U0001f4d6",    "Commands",      "Other"),
    ("hotkeys",  "\u2328\ufe0f",  "Hotkeys",       None),
    ("advanced", "\u2699\ufe0f",  "Advanced",      None),
    ("about",    "\u2139\ufe0f",  "About",         None),
)


class SettingsWindow(QMainWindow):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        voices_dir: Path | None = None,
        on_test_voice: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Jarvis Settings")
        self.resize(*_DEFAULT_SIZE)

        self.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose, on=False
        )

        apply_theme(self)

        # ── Status bar ─────────────────────────────────────────────
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        def _on_change_with_indicator() -> None:
            self._status_bar.showMessage("\u2713 Saved", 1500)
            on_change()

        # ── Build tabs ─────────────────────────────────────────────
        self.general = GeneralTab(config=config, on_change=_on_change_with_indicator)
        self.voice = VoiceTab(
            config=config,
            on_change=_on_change_with_indicator,
            voices_dir=voices_dir,
            on_test_voice=on_test_voice,
        )
        self.models = ModelsTab(config=config, on_change=_on_change_with_indicator)
        self.tools = ToolsTab(config=config, on_change=_on_change_with_indicator)
        self.coding = CodingTab(config=config, on_change=_on_change_with_indicator)
        self.memory = MemoryTab(config=config, on_change=_on_change_with_indicator)
        self.gaming = GamingTab(config=config, on_change=_on_change_with_indicator)
        self.image_gen = ImageGenTab(config=config, on_change=_on_change_with_indicator)
        self.security = SecurityTab(config=config, on_change=_on_change_with_indicator)
        self.commands = CommandsTab(config=config, on_change=_on_change_with_indicator)
        self.hotkeys = HotkeysTab(config=config, on_change=_on_change_with_indicator)
        self.advanced = AdvancedTab(config=config, on_change=_on_change_with_indicator)
        self.about = AboutTab(config=config, on_change=_on_change_with_indicator)

        self._pages: dict[str, QWidget] = {
            "general": self.general,
            "voice": self.voice,
            "models": self.models,
            "tools": self.tools,
            "coding": self.coding,
            "memory": self.memory,
            "gaming": self.gaming,
            "image_gen": self.image_gen,
            "security": self.security,
            "commands": self.commands,
            "hotkeys": self.hotkeys,
            "advanced": self.advanced,
            "about": self.about,
        }

        # ── Stacked widget for pages ───────────────────────────────
        self._stack = QStackedWidget()
        for key, _icon, _label, _sec in _NAV_ITEMS:
            widget = self._pages[key]
            # Wrap in scroll area
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._stack.addWidget(scroll)

        # ── Sidebar ────────────────────────────────────────────────
        sidebar = self._build_sidebar()

        # ── Central layout ─────────────────────────────────────────
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        # Select first page
        self._select_page("general")

    # ── Sidebar builder ────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(220)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("sidebarHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 16)

        title = QLabel("\U0001f916  J.A.R.V.I.S.")
        title.setObjectName("sidebarTitle")
        version = QLabel("v1.0.0 \u00b7 Schema v34")
        version.setObjectName("sidebarVersion")

        header_layout.addWidget(title)
        header_layout.addWidget(version)
        layout.addWidget(header)
        layout.addSpacing(8)

        # Nav items
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, icon, label, section in _NAV_ITEMS:
            if section is not None:
                sec_label = QLabel(section.upper())
                sec_label.setObjectName("navSection")
                sec_label.setContentsMargins(12, 8, 12, 4)
                layout.addWidget(sec_label)

            btn = QPushButton(f"  {icon}  {label}")
            btn.setObjectName("navItem")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self._select_page(k))
            layout.addWidget(btn)
            self._nav_buttons[key] = btn

        layout.addStretch()

        # Footer with status
        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)

        status_dot = QLabel("\u25cf")
        status_dot.setStyleSheet("color: #22c55e; font-size: 10px;")
        status_label = QLabel("Ollama connected")
        status_label.setObjectName("statusLabel")

        footer_layout.addWidget(status_dot)
        footer_layout.addWidget(status_label)
        footer_layout.addStretch()
        layout.addWidget(footer)

        return sidebar

    # ── Page selection ─────────────────────────────────────────────

    def _select_page(self, key: str) -> None:
        """Switch to the named page and update nav button styles."""
        idx = next(
            (i for i, (k, *_) in enumerate(_NAV_ITEMS) if k == key),
            0,
        )
        self._stack.setCurrentIndex(idx)

        # Update button styles
        for bkey, btn in self._nav_buttons.items():
            if bkey == key:
                btn.setObjectName("navItemActive")
            else:
                btn.setObjectName("navItem")
            # Force style recalculation
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── Persistence ────────────────────────────────────────────────

    def _autosave(self) -> None:
        try:
            save_config(self._config)
            self._status_bar.showMessage("\u2713 Saved", 1500)
        except Exception:
            log.exception("autosave failed from SettingsWindow")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._autosave()
        event.ignore()
        self.hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._autosave()
        super().hideEvent(event)
