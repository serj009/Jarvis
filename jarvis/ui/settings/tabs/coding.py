"""Coding Agent settings tab: provider, model, API keys, agent behavior."""

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

_PROVIDERS = [
    ("main", "Main LLM (Ollama)"),
    ("ollama", "Ollama Local"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("groq", "Groq"),
]

_PROVIDER_INDEX = {key: i for i, (key, _) in enumerate(_PROVIDERS)}


class CodingTab(QWidget):
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

        title = QLabel("Coding Agent")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Configure the LLM used for code generation, editing, and review"
        )
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Model card --
        model_card = SectionCard()
        model_card.add_header("\U0001f9e0", "Coding Agent Model")

        # Provider selector
        self._provider_combo = QComboBox()
        for _key, label in _PROVIDERS:
            self._provider_combo.addItem(label)
        current_idx = _PROVIDER_INDEX.get(config.coding.provider, 0)
        self._provider_combo.setCurrentIndex(current_idx)
        model_card.add_row("Provider", "Which LLM backend to use", self._provider_combo)

        # Ollama model
        self._ollama_model = QLineEdit(config.coding.ollama_model)
        self._ollama_model.setPlaceholderText("e.g. qwen2.5-coder:7b")
        model_card.add_row(
            "Ollama model",
            "Local model name (when provider = Ollama)",
            self._ollama_model,
        )

        # API model
        self._api_model = QLineEdit(config.coding.api_model)
        self._api_model.setPlaceholderText(
            "e.g. gpt-4o, claude-3-5-sonnet-20241022, llama-3.3-70b-versatile"
        )
        model_card.add_row(
            "API model",
            "Model name for cloud providers",
            self._api_model,
        )

        # API key
        self._api_key = QLineEdit(config.coding.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-... / anthropic key / groq key")
        model_card.add_row(
            "API key",
            "Encrypted at rest (DPAPI)",
            self._api_key,
        )

        # API base URL
        self._api_base_url = QLineEdit(config.coding.api_base_url)
        self._api_base_url.setPlaceholderText(
            "Custom endpoint (optional, leave blank for default)"
        )
        model_card.add_row(
            "API base URL",
            "Override for OpenAI-compatible endpoints",
            self._api_base_url,
            last=True,
        )

        root.addWidget(model_card)

        # -- Behavior card --
        behavior_card = SectionCard()
        behavior_card.add_header("\u2699\ufe0f", "Agent Behavior")

        # Max iterations slider
        self._iter_slider = QSlider(Qt.Orientation.Horizontal)
        self._iter_slider.setRange(1, 50)
        self._iter_slider.setValue(config.coding.max_iterations)
        self._iter_label = QLabel(str(config.coding.max_iterations))
        self._iter_label.setObjectName("settingHint")
        self._iter_label.setMinimumWidth(30)
        behavior_card.add_row(
            "Max iterations",
            "Maximum coding agent loop iterations per task (1–50)",
            slider_with_label(self._iter_slider, self._iter_label),
        )

        # Auto-commit toggle
        self._auto_commit = ToggleSwitch(checked=config.coding.auto_commit)
        behavior_card.add_row(
            "Auto-commit",
            "Automatically git-commit after successful code changes",
            self._auto_commit,
        )

        # Default workspace
        self._default_workspace = QLineEdit(config.coding.default_workspace)
        self._default_workspace.setPlaceholderText(
            "Default project directory (optional)"
        )
        behavior_card.add_row(
            "Default workspace",
            "Working directory for the coding agent",
            self._default_workspace,
            last=True,
        )

        root.addWidget(behavior_card)
        root.addStretch(1)

        # -- Apply initial visibility --
        self._update_field_visibility()

        # -- Connections --
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._ollama_model.editingFinished.connect(self._on_ollama_model)
        self._api_model.editingFinished.connect(self._on_api_model)
        self._api_key.editingFinished.connect(self._on_api_key)
        self._api_base_url.editingFinished.connect(self._on_api_base_url)
        self._iter_slider.valueChanged.connect(self._on_max_iterations)
        self._auto_commit.toggled.connect(self._on_auto_commit)
        self._default_workspace.editingFinished.connect(self._on_default_workspace)

    # -- Visibility helpers --

    def _update_field_visibility(self) -> None:
        provider = _PROVIDERS[self._provider_combo.currentIndex()][0]
        is_ollama = provider == "ollama"
        is_api = provider in ("openai", "anthropic", "groq")

        self._ollama_model.setVisible(is_ollama)
        # Find the parent row labels — hide via layout traversal
        # Simple approach: just setEnabled
        self._ollama_model.setEnabled(is_ollama)
        self._api_model.setEnabled(is_api)
        self._api_key.setEnabled(is_api)
        self._api_base_url.setEnabled(is_api and provider == "openai")

    # -- Handlers --

    def _on_provider_changed(self, index: int) -> None:
        provider_key = _PROVIDERS[index][0]
        self._config.coding.provider = provider_key
        self._update_field_visibility()
        self._persist()

    def _on_ollama_model(self) -> None:
        self._config.coding.ollama_model = self._ollama_model.text().strip()
        self._persist()

    def _on_api_model(self) -> None:
        self._config.coding.api_model = self._api_model.text().strip()
        self._persist()

    def _on_api_key(self) -> None:
        self._config.coding.api_key = self._api_key.text().strip()
        self._persist()

    def _on_api_base_url(self) -> None:
        self._config.coding.api_base_url = self._api_base_url.text().strip()
        self._persist()

    def _on_max_iterations(self, value: int) -> None:
        self._config.coding.max_iterations = value
        self._iter_label.setText(str(value))
        self._persist()

    def _on_auto_commit(self) -> None:
        self._config.coding.auto_commit = self._auto_commit.isChecked()
        self._persist()

    def _on_default_workspace(self) -> None:
        self._config.coding.default_workspace = (
            self._default_workspace.text().strip()
        )
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from CodingTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from CodingTab")
