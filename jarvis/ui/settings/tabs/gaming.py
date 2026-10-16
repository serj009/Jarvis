"""Game Mode settings tab: detection, VRAM behavior, custom games."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import Badge, SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)


class GamingTab(QWidget):
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

        title = QLabel("Game Mode")
        title.setObjectName("pageTitle")
        desc = QLabel("Automatic VRAM management when games are running")
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Detection card --
        detect_card = SectionCard()
        detect_card.add_header(
            "\U0001f3ae", "Game Detection",
        )

        self.enabled = ToggleSwitch(checked=config.game_mode.enabled)
        detect_card.add_row(
            "Enable Game Mode",
            "Auto-detect games and free GPU memory",
            self.enabled,
        )

        self.scan_interval = QSlider(Qt.Orientation.Horizontal)
        self.scan_interval.setRange(5, 120)
        self.scan_interval.setValue(config.game_mode.scan_interval_seconds)
        self.scan_label = QLabel(f"{config.game_mode.scan_interval_seconds} sec")
        self.scan_label.setObjectName("settingHint")
        self.scan_label.setMinimumWidth(50)
        detect_card.add_row(
            "Scan interval",
            "How often to check for new games (seconds)",
            slider_with_label(self.scan_interval, self.scan_label),
            last=True,
        )

        root.addWidget(detect_card)

        # -- VRAM card --
        vram_card = SectionCard()
        vram_card.add_header(
            "\u26a1", "VRAM Behavior",
            badge=Badge("GPU", "info"),
        )

        # These are hardcoded behaviors (always happens in game mode)
        # Shown as info toggles
        self.evict_llm = ToggleSwitch(checked=True)
        self.evict_llm.setEnabled(False)
        vram_card.add_row(
            "Evict LLM on game start",
            "Unload Qwen from GPU for maximum game VRAM",
            self.evict_llm,
        )

        self.switch_stt = ToggleSwitch(checked=True)
        self.switch_stt.setEnabled(False)
        vram_card.add_row(
            "Switch STT to CPU",
            "Use tiny model on CPU during games",
            self.switch_stt,
        )

        self.preload = ToggleSwitch(checked=True)
        self.preload.setEnabled(False)
        vram_card.add_row(
            "Preload game memories",
            "Load tips and save info for the detected game",
            self.preload,
            last=True,
        )

        root.addWidget(vram_card)

        # -- Custom games card --
        custom_card = SectionCard()
        custom_card.add_header(
            "\U0001f4cb", "Custom Games",
        )

        lbl = QLabel(
            "Additional game process names that trigger full VRAM eviction "
            "(one per line, without .exe):"
        )
        lbl.setWordWrap(True)
        lbl.setObjectName("settingHint")
        custom_card.add_widget(lbl)

        self.custom_games = QPlainTextEdit()
        self.custom_games.setPlaceholderText(
            "e.g.\nMyCustomGame\nSomeOtherGame"
        )
        self.custom_games.setMaximumHeight(100)
        self.custom_games.setPlainText(
            "\n".join(config.game_mode.custom_main_games)
        )
        custom_card.add_widget(self.custom_games)

        save_btn = QPushButton("Save custom games")
        save_btn.setObjectName("btnSmall")
        save_btn.clicked.connect(self._on_save_custom)
        custom_card.add_widget(save_btn)

        root.addWidget(custom_card)
        root.addStretch(1)

        # -- Connections --
        self.enabled.toggled.connect(self._on_enabled)
        self.scan_interval.valueChanged.connect(self._on_scan_interval)

    def _on_enabled(self) -> None:
        self._config.game_mode.enabled = self.enabled.isChecked()
        self._persist()

    def _on_scan_interval(self, value: int) -> None:
        self._config.game_mode.scan_interval_seconds = value
        self.scan_label.setText(f"{value} sec")
        self._persist()

    def _on_save_custom(self) -> None:
        lines = [
            l.strip()
            for l in self.custom_games.toPlainText().splitlines()
            if l.strip()
        ]
        self._config.game_mode.custom_main_games = lines
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from GamingTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from GamingTab")
