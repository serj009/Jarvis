"""Proactive engine settings tab — initiative level + thresholds."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)

_LEVEL_ITEMS: tuple[tuple[str, str], ...] = (
    ("high", "High — suggests actively"),
    ("medium", "Medium — only important things"),
    ("low", "Low — only critical"),
    ("silent", "Silent — never unprompted"),
)


class ProactiveTab(QWidget):
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
        pr = config.proactive

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        title = QLabel("Proactive (Initiative)")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Let Jarvis speak up on its own \u2014 stale projects, upcoming "
            "birthdays, long work sessions, and your boot routine. It "
            "quiets down automatically if you keep dismissing suggestions; "
            "say 'be more active' to raise it again."
        )
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- control card --
        card = SectionCard()
        card.add_header("\U0001f4a1", "Initiative")

        self._enabled = ToggleSwitch(checked=pr.enabled)
        card.add_row(
            "Enable proactive suggestions",
            "Master switch for all unprompted suggestions",
            self._enabled,
        )

        self._level = QComboBox()
        for value, label in _LEVEL_ITEMS:
            self._level.addItem(label, value)
        idx = self._level.findData(pr.initiative_level)
        if idx >= 0:
            self._level.setCurrentIndex(idx)
        card.add_row(
            "Initiative level",
            "How assertively Jarvis offers suggestions",
            self._level,
            last=True,
        )
        root.addWidget(card)

        # -- thresholds card --
        thr = SectionCard()
        thr.add_header("\u2699\ufe0f", "Thresholds")

        self._work = QSlider(Qt.Orientation.Horizontal)
        self._work.setRange(1, 16)  # hours
        self._work.setValue(int(pr.work_hours_threshold))
        self._work_label = QLabel(f"{int(pr.work_hours_threshold)} h")
        self._work_label.setObjectName("settingHint")
        self._work_label.setMinimumWidth(40)
        thr.add_row(
            "Long-work nudge after",
            "Hours of continuous focus before a health break is suggested",
            slider_with_label(self._work, self._work_label),
        )

        self._leisure = QSlider(Qt.Orientation.Horizontal)
        self._leisure.setRange(1, 12)
        self._leisure.setValue(int(pr.leisure_hours_threshold))
        self._leisure_label = QLabel(f"{int(pr.leisure_hours_threshold)} h")
        self._leisure_label.setObjectName("settingHint")
        self._leisure_label.setMinimumWidth(40)
        thr.add_row(
            "Stale-project nudge after",
            "Hours of leisure (games/video) before nudging about a project",
            slider_with_label(self._leisure, self._leisure_label),
            last=True,
        )
        root.addWidget(thr)
        root.addStretch()

        # -- signals --
        self._enabled.toggled.connect(self._save_enabled)
        self._level.currentIndexChanged.connect(self._save_level)
        self._work.valueChanged.connect(self._save_work)
        self._leisure.valueChanged.connect(self._save_leisure)

    # -- persistence --

    def _save_enabled(self, val: bool) -> None:
        self._config.proactive.enabled = val
        self._persist()

    def _save_level(self) -> None:
        self._config.proactive.initiative_level = self._level.currentData()
        self._persist()

    def _save_work(self, val: int) -> None:
        self._config.proactive.work_hours_threshold = float(val)
        self._work_label.setText(f"{val} h")
        self._persist()

    def _save_leisure(self, val: int) -> None:
        self._config.proactive.leisure_hours_threshold = float(val)
        self._leisure_label.setText(f"{val} h")
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("Failed to save proactive config")
        self._on_change()
