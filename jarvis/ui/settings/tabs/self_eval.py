"""Self-Evaluation (auto-critic) settings tab.

OPT-IN, OFF BY DEFAULT. Lets the user enable an external critic LLM that
periodically grades interaction traces and writes a report. Exposes the
toggle, provider, API key (DPAPI-encrypted), model, cadence, trace cap,
and whether to save the critic's insights into memory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)

_PROVIDER_ITEMS: tuple[tuple[str, str], ...] = (
    ("groq", "Groq (free tier available)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("openai_compatible", "OpenAI-compatible (custom)"),
)


class SelfEvalTab(QWidget):
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
        se = config.self_evaluation

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        title = QLabel("Self-Evaluation")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Optional. An EXTERNAL critic LLM periodically grades how well "
            "Jarvis handled recent requests and writes a report. Off by "
            "default \u2014 the local pipeline needs no internet. Secrets "
            "(keys/passwords/logins) are stripped from traces before they "
            "leave your PC. The critic only reports; it never changes "
            "Jarvis's behaviour on its own."
        )
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Enable card --
        enable_card = SectionCard()
        enable_card.add_header("\U0001f9ea", "Critic Control")

        self._enabled = ToggleSwitch(checked=se.enabled)
        enable_card.add_row(
            "Enable self-evaluation",
            "Send recent interaction traces to an external critic on a schedule",
            self._enabled,
        )

        self._save_insights = ToggleSwitch(checked=se.save_insights_to_memory)
        enable_card.add_row(
            "Save insights to memory",
            "Store the critic's observations as uncertain facts you can confirm later",
            self._save_insights,
            last=True,
        )
        root.addWidget(enable_card)

        # -- Critic API card --
        api_card = SectionCard()
        api_card.add_header("\U0001f310", "Critic Model (external API)")

        self._provider = QComboBox()
        for value, label in _PROVIDER_ITEMS:
            self._provider.addItem(label, value)
        idx = self._provider.findData(se.provider)
        if idx >= 0:
            self._provider.setCurrentIndex(idx)
        api_card.add_row(
            "Provider", "Which external critic service to use", self._provider,
        )

        self._api_model = QLineEdit(se.api_model)
        self._api_model.setPlaceholderText("e.g. llama-3.3-70b-versatile, gpt-4o")
        api_card.add_row(
            "Model", "Critic model name", self._api_model,
        )

        self._api_key = QLineEdit(se.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Required when enabled \u2014 encrypted at rest (DPAPI)")
        api_card.add_row(
            "API Key", "Your critic API key; stored encrypted", self._api_key,
        )

        self._api_base_url = QLineEdit(se.api_base_url)
        self._api_base_url.setPlaceholderText("(only for OpenAI-compatible)")
        api_card.add_row(
            "Base URL", "Custom endpoint for OpenAI-compatible providers",
            self._api_base_url, last=True,
        )
        root.addWidget(api_card)

        # -- Schedule card --
        sched_card = SectionCard()
        sched_card.add_header("\u23f1\ufe0f", "Schedule & Budget")

        # interval_hours: 0.5 .. 168 (slider in half-hours → store float hours)
        self._interval_slider = QSlider(Qt.Orientation.Horizontal)
        self._interval_slider.setRange(1, 336)  # units of 0.5 h → 0.5 .. 168 h
        self._interval_slider.setValue(int(round(se.interval_hours * 2)))
        self._interval_label = QLabel(self._fmt_interval(se.interval_hours))
        self._interval_label.setObjectName("settingHint")
        self._interval_label.setMinimumWidth(70)
        sched_card.add_row(
            "Run every",
            "How often the critic evaluates recent traces (0.5 h \u2013 7 days)",
            slider_with_label(self._interval_slider, self._interval_label),
        )

        self._max_traces = QSlider(Qt.Orientation.Horizontal)
        self._max_traces.setRange(1, 200)
        self._max_traces.setValue(se.max_traces_per_run)
        self._max_traces_label = QLabel(str(se.max_traces_per_run))
        self._max_traces_label.setObjectName("settingHint")
        self._max_traces_label.setMinimumWidth(40)
        sched_card.add_row(
            "Max traces per run",
            "Cap traces sent per run to bound token cost on free tiers (1\u2013200)",
            slider_with_label(self._max_traces, self._max_traces_label),
            last=True,
        )
        root.addWidget(sched_card)

        root.addStretch()

        # -- Signals --
        self._enabled.toggled.connect(self._save_enabled)
        self._save_insights.toggled.connect(self._save_save_insights)
        self._provider.currentIndexChanged.connect(self._save_provider)
        self._api_model.editingFinished.connect(self._save_api_model)
        self._api_key.editingFinished.connect(self._save_api_key)
        self._api_base_url.editingFinished.connect(self._save_api_base_url)
        self._interval_slider.valueChanged.connect(self._save_interval)
        self._max_traces.valueChanged.connect(self._save_max_traces)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _fmt_interval(hours: float) -> str:
        if hours >= 24:
            days = hours / 24
            return f"{days:.0f} d" if days == int(days) else f"{days:.1f} d"
        if hours >= 1:
            return f"{hours:.0f} h" if hours == int(hours) else f"{hours:.1f} h"
        return f"{int(hours * 60)} min"

    # -- persistence -------------------------------------------------------

    def _save_enabled(self, val: bool) -> None:
        self._config.self_evaluation.enabled = val
        self._persist()

    def _save_save_insights(self, val: bool) -> None:
        self._config.self_evaluation.save_insights_to_memory = val
        self._persist()

    def _save_provider(self) -> None:
        self._config.self_evaluation.provider = self._provider.currentData()
        self._persist()

    def _save_api_model(self) -> None:
        self._config.self_evaluation.api_model = self._api_model.text().strip()
        self._persist()

    def _save_api_key(self) -> None:
        self._config.self_evaluation.api_key = self._api_key.text().strip()
        self._persist()

    def _save_api_base_url(self) -> None:
        self._config.self_evaluation.api_base_url = self._api_base_url.text().strip()
        self._persist()

    def _save_interval(self, val: int) -> None:
        hours = val / 2.0
        self._config.self_evaluation.interval_hours = hours
        self._interval_label.setText(self._fmt_interval(hours))
        self._persist()

    def _save_max_traces(self, val: int) -> None:
        self._config.self_evaluation.max_traces_per_run = val
        self._max_traces_label.setText(str(val))
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("Failed to save self-evaluation config")
        self._on_change()
