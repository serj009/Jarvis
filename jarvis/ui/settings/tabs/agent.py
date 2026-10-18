"""Autonomous Agent settings tab: enable, limits, confirmation, reasoning."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)


class AgentTab(QWidget):
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

        title = QLabel("Autonomous Agent")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Configure the general-purpose plan-and-execute agent that "
            "chains tools to accomplish complex multi-step goals"
        )
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Enable card --
        enable_card = SectionCard()
        enable_card.add_header("🤖", "Agent Control")

        self._enabled = ToggleSwitch(checked=config.agent.enabled)
        enable_card.add_row(
            "Enable agent",
            "Allow the autonomous agent to plan and execute tasks",
            self._enabled,
        )

        self._confirm = ToggleSwitch(checked=config.agent.confirm_destructive)
        enable_card.add_row(
            "Confirm destructive",
            "Ask before deleting files, running system commands, etc.",
            self._confirm,
        )

        self._verbose = ToggleSwitch(checked=config.agent.verbose_reasoning)
        enable_card.add_row(
            "Verbose reasoning",
            "Show every thought step in the panel and logs",
            self._verbose,
            last=True,
        )

        root.addWidget(enable_card)

        # -- Limits card --
        limits_card = SectionCard()
        limits_card.add_header("⚙️", "Limits")

        # Max iterations slider (1-100)
        self._iter_slider = QSlider(Qt.Orientation.Horizontal)
        self._iter_slider.setRange(1, 100)
        self._iter_slider.setValue(config.agent.max_iterations)
        self._iter_label = QLabel(str(config.agent.max_iterations))
        self._iter_label.setObjectName("settingHint")
        self._iter_label.setMinimumWidth(30)
        limits_card.add_row(
            "Max iterations",
            "Maximum agent loop iterations per task (1–100)",
            slider_with_label(self._iter_slider, self._iter_label),
        )

        # Max time slider (30-3600 seconds)
        self._time_slider = QSlider(Qt.Orientation.Horizontal)
        self._time_slider.setRange(30, 3600)
        self._time_slider.setSingleStep(30)
        self._time_slider.setValue(config.agent.max_time_seconds)
        secs = config.agent.max_time_seconds
        time_text = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
        self._time_label = QLabel(time_text)
        self._time_label.setObjectName("settingHint")
        self._time_label.setMinimumWidth(50)
        limits_card.add_row(
            "Max time",
            "Maximum wall-clock time per task (30 s – 60 min)",
            slider_with_label(self._time_slider, self._time_label),
            last=True,
        )

        root.addWidget(limits_card)

        root.addStretch()

        # -- Connect signals --
        self._enabled.toggled.connect(self._save_enabled)
        self._confirm.toggled.connect(self._save_confirm)
        self._verbose.toggled.connect(self._save_verbose)
        self._iter_slider.valueChanged.connect(self._save_iter)
        self._time_slider.valueChanged.connect(self._save_time)

    # -- Persistence helpers -----------------------------------------------

    def _save_enabled(self, val: bool) -> None:
        self._config.agent.enabled = val
        self._persist()

    def _save_confirm(self, val: bool) -> None:
        self._config.agent.confirm_destructive = val
        self._persist()

    def _save_verbose(self, val: bool) -> None:
        self._config.agent.verbose_reasoning = val
        self._persist()

    def _save_iter(self, val: int) -> None:
        self._config.agent.max_iterations = val
        self._iter_label.setText(str(val))
        self._persist()

    def _save_time(self, val: int) -> None:
        self._config.agent.max_time_seconds = val
        if val >= 60:
            self._time_label.setText(f"{val // 60}m {val % 60}s")
        else:
            self._time_label.setText(f"{val}s")
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("Failed to save agent config")
        self._on_change()
