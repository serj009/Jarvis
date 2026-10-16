"""Advanced settings tab: pipeline, research, debug. Power-user territory."""

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
from jarvis.ui.settings._widgets import Badge, SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)


class AdvancedTab(QWidget):
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

        title = QLabel("Advanced")
        title.setObjectName("pageTitle")
        desc = QLabel("Power-user settings — change only if you know what you're doing")
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Pipeline card --
        pipe_card = SectionCard()
        pipe_card.add_header(
            "\U0001f4bb", "Pipeline",
            badge=Badge("Audio loop", "info"),
        )

        self.stt_timeout = QSlider(Qt.Orientation.Horizontal)
        self.stt_timeout.setRange(5, 30)
        self.stt_timeout.setValue(int(config.pipeline.stt_timeout_s))
        self.stt_timeout_label = QLabel(f"{config.pipeline.stt_timeout_s:.0f}s")
        self.stt_timeout_label.setObjectName("settingHint")
        self.stt_timeout_label.setMinimumWidth(30)
        pipe_card.add_row(
            "STT timeout",
            "Max seconds for speech recognition per turn",
            slider_with_label(self.stt_timeout, self.stt_timeout_label),
        )

        self.llm_timeout = QSlider(Qt.Orientation.Horizontal)
        self.llm_timeout.setRange(5, 60)
        self.llm_timeout.setValue(int(config.pipeline.llm_timeout_s))
        self.llm_timeout_label = QLabel(f"{config.pipeline.llm_timeout_s:.0f}s")
        self.llm_timeout_label.setObjectName("settingHint")
        self.llm_timeout_label.setMinimumWidth(30)
        pipe_card.add_row(
            "LLM timeout",
            "Max seconds for language model response",
            slider_with_label(self.llm_timeout, self.llm_timeout_label),
        )

        self.tts_timeout = QSlider(Qt.Orientation.Horizontal)
        self.tts_timeout.setRange(5, 30)
        self.tts_timeout.setValue(int(config.pipeline.tts_timeout_s))
        self.tts_timeout_label = QLabel(f"{config.pipeline.tts_timeout_s:.0f}s")
        self.tts_timeout_label.setObjectName("settingHint")
        self.tts_timeout_label.setMinimumWidth(30)
        pipe_card.add_row(
            "TTS timeout",
            "Max seconds for text-to-speech synthesis",
            slider_with_label(self.tts_timeout, self.tts_timeout_label),
            last=True,
        )

        root.addWidget(pipe_card)

        # -- Debug card --
        debug_card = SectionCard()
        debug_card.add_header(
            "\U0001f41b", "Debug",
        )

        self.log_wake = ToggleSwitch(checked=config.debug.log_wake_during_speaking)
        debug_card.add_row(
            "Log wake-word during speaking",
            "Diagnostic: log detection scores while JARVIS is speaking",
            self.log_wake,
            last=True,
        )

        root.addWidget(debug_card)
        root.addStretch(1)

        # -- Connections --
        self.stt_timeout.valueChanged.connect(self._on_stt_timeout)
        self.llm_timeout.valueChanged.connect(self._on_llm_timeout)
        self.tts_timeout.valueChanged.connect(self._on_tts_timeout)
        self.log_wake.toggled.connect(self._on_log_wake)

    def _on_stt_timeout(self, value: int) -> None:
        self._config.pipeline.stt_timeout_s = float(value)
        self.stt_timeout_label.setText(f"{value}s")
        self._persist()

    def _on_llm_timeout(self, value: int) -> None:
        self._config.pipeline.llm_timeout_s = float(value)
        self.llm_timeout_label.setText(f"{value}s")
        self._persist()

    def _on_tts_timeout(self, value: int) -> None:
        self._config.pipeline.tts_timeout_s = float(value)
        self.tts_timeout_label.setText(f"{value}s")
        self._persist()

    def _on_log_wake(self) -> None:
        self._config.debug.log_wake_during_speaking = self.log_wake.isChecked()
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from AdvancedTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from AdvancedTab")
