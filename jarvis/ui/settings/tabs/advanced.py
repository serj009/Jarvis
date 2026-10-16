"""Advanced settings tab: pipeline, deep research, debug. Power-user territory."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
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

        # -- Deep Research card --
        research_card = SectionCard()
        research_card.add_header(
            "\U0001f50e", "Deep Research",
            badge=Badge("Ultra", "info"),
        )

        self.research_planner_model = QLineEdit(config.research.planner_model)
        self.research_planner_model.setPlaceholderText(
            "e.g. qwen2.5:7b-instruct (blank = main model)"
        )
        self.research_planner_model.setMinimumWidth(220)
        research_card.add_row(
            "Planner model",
            "Designs the research plan. Leave blank to use main LLM",
            self.research_planner_model,
        )

        self.research_worker_model = QLineEdit(config.research.worker_model)
        self.research_worker_model.setPlaceholderText(
            "e.g. qwen2.5:3b-instruct (blank = main model)"
        )
        self.research_worker_model.setMinimumWidth(220)
        research_card.add_row(
            "Worker model",
            "Extracts bullet points from pages. Leave blank to use main LLM",
            self.research_worker_model,
        )

        research_card.add_divider()

        self.ultra_enabled = ToggleSwitch(checked=config.research.ultra_enabled)
        research_card.add_row(
            "Enable Ultra mode",
            "Groq cloud LLM + Brave Search for higher quality research",
            self.ultra_enabled,
        )

        self.ultra_planner_model = QLineEdit(config.research.ultra_planner_model)
        self.ultra_planner_model.setPlaceholderText("groq/llama-3.3-70b-versatile")
        self.ultra_planner_model.setMinimumWidth(220)
        research_card.add_row(
            "Ultra planner model",
            "Cloud model used as planner in Ultra mode",
            self.ultra_planner_model,
        )

        self.ultra_gap_fill = QSlider(Qt.Orientation.Horizontal)
        self.ultra_gap_fill.setRange(1, 5)
        self.ultra_gap_fill.setValue(config.research.ultra_gap_fill_iterations)
        self.ultra_gap_fill_label = QLabel(
            str(config.research.ultra_gap_fill_iterations)
        )
        self.ultra_gap_fill_label.setObjectName("settingHint")
        self.ultra_gap_fill_label.setMinimumWidth(20)
        research_card.add_row(
            "Gap-fill iterations",
            "Number of planner review + re-search passes (1–5)",
            slider_with_label(self.ultra_gap_fill, self.ultra_gap_fill_label),
        )

        self.groq_api_key = QLineEdit(config.research.groq_api_key)
        self.groq_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_api_key.setPlaceholderText("or set JARVIS_GROQ_API_KEY env var")
        self.groq_api_key.setMinimumWidth(220)
        research_card.add_row(
            "Groq API Key",
            "Required for Ultra planner. Free at console.groq.com",
            self.groq_api_key,
        )

        self.brave_api_key = QLineEdit(config.research.brave_api_key)
        self.brave_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.brave_api_key.setPlaceholderText("or set JARVIS_BRAVE_API_KEY env var")
        self.brave_api_key.setMinimumWidth(220)
        research_card.add_row(
            "Brave API Key",
            "Optional. Falls back to DuckDuckGo when missing",
            self.brave_api_key,
        )

        ultra_hint = QLabel(
            "Ultra mode uses Groq cloud API for higher quality research. "
            "Requires internet + free API key from console.groq.com"
        )
        ultra_hint.setObjectName("settingHint")
        ultra_hint.setWordWrap(True)
        research_card.add_widget(ultra_hint)

        root.addWidget(research_card)

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

        # Deep Research connections
        self.research_planner_model.editingFinished.connect(
            self._on_research_planner_model
        )
        self.research_worker_model.editingFinished.connect(
            self._on_research_worker_model
        )
        self.ultra_enabled.toggled.connect(self._on_ultra_enabled)
        self.ultra_planner_model.editingFinished.connect(
            self._on_ultra_planner_model
        )
        self.ultra_gap_fill.valueChanged.connect(self._on_ultra_gap_fill)
        self.groq_api_key.editingFinished.connect(self._on_groq_api_key)
        self.brave_api_key.editingFinished.connect(self._on_brave_api_key)

    # -- Pipeline handlers --

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

    # -- Deep Research handlers --

    def _on_research_planner_model(self) -> None:
        self._config.research.planner_model = (
            self.research_planner_model.text().strip()
        )
        self._persist()

    def _on_research_worker_model(self) -> None:
        self._config.research.worker_model = (
            self.research_worker_model.text().strip()
        )
        self._persist()

    def _on_ultra_enabled(self) -> None:
        self._config.research.ultra_enabled = self.ultra_enabled.isChecked()
        self._persist()

    def _on_ultra_planner_model(self) -> None:
        self._config.research.ultra_planner_model = (
            self.ultra_planner_model.text().strip()
            or "groq/llama-3.3-70b-versatile"
        )
        self._persist()

    def _on_ultra_gap_fill(self, value: int) -> None:
        self._config.research.ultra_gap_fill_iterations = value
        self.ultra_gap_fill_label.setText(str(value))
        self._persist()

    def _on_groq_api_key(self) -> None:
        self._config.research.groq_api_key = self.groq_api_key.text().strip()
        self._persist()

    def _on_brave_api_key(self) -> None:
        self._config.research.brave_api_key = self.brave_api_key.text().strip()
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
