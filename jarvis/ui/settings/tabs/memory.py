"""Memory settings tab: embeddings toggle, tier thresholds."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import Badge, SectionCard, slider_with_label

log = logging.getLogger(__name__)


class MemoryTab(QWidget):
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

        # -- Page header --
        title = QLabel("Memory")
        title.setObjectName("pageTitle")
        desc = QLabel("How JARVIS remembers facts and conversations")
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Embeddings card --
        embed_card = SectionCard()
        embed_card.add_header(
            "\U0001f9e0", "Semantic Search",
            badge=Badge("FAISS", "info"),
        )

        self.embeddings_enabled = QCheckBox()
        self.embeddings_enabled.setChecked(config.memory.embeddings_enabled)
        embed_card.add_row(
            "Enable embeddings",
            "Use FAISS + multilingual model for semantic memory search (~420 MB)",
            self.embeddings_enabled,
        )

        self.embed_device = QComboBox()
        self.embed_device.addItems(["cpu", "cuda"])
        self.embed_device.setCurrentText(config.memory.embeddings_device)
        embed_card.add_row(
            "Embeddings device",
            "Where to run the embedding model",
            self.embed_device,
        )

        self.embed_top_k = QSlider(Qt.Orientation.Horizontal)
        self.embed_top_k.setRange(5, 50)
        self.embed_top_k.setValue(config.memory.embeddings_top_k)
        self.embed_top_k_label = QLabel(str(config.memory.embeddings_top_k))
        self.embed_top_k_label.setObjectName("settingHint")
        self.embed_top_k_label.setMinimumWidth(30)
        embed_card.add_row(
            "Top K results",
            "Number of similar facts to retrieve per query",
            slider_with_label(self.embed_top_k, self.embed_top_k_label),
            last=True,
        )

        root.addWidget(embed_card)

        # -- Behavior card --
        # Note: auto_save, auto_recall, learn_tool_examples are NOT
        # in MemoryConfig (they're hardcoded always-on behaviors).
        # We show them as read-only info for now.
        info_card = SectionCard()
        info_card.add_header(
            "\u2699\ufe0f", "Automatic Behaviors",
            badge=Badge("Always On", "ok"),
        )

        self.auto_save = QCheckBox()
        self.auto_save.setChecked(True)
        self.auto_save.setEnabled(False)  # Not configurable yet
        info_card.add_row(
            "Auto-save facts",
            "Extract important facts from conversations automatically",
            self.auto_save,
        )

        self.auto_recall = QCheckBox()
        self.auto_recall.setChecked(True)
        self.auto_recall.setEnabled(False)
        info_card.add_row(
            "Auto-recall",
            "Inject relevant memories into every conversation",
            self.auto_recall,
        )

        self.learn_examples = QCheckBox()
        self.learn_examples.setChecked(True)
        self.learn_examples.setEnabled(False)
        info_card.add_row(
            "Learn tool examples",
            "Remember successful commands as few-shot examples",
            self.learn_examples,
            last=True,
        )

        root.addWidget(info_card)
        root.addStretch(1)

        # -- Connections --
        self.embeddings_enabled.stateChanged.connect(self._on_embeddings)
        self.embed_device.currentTextChanged.connect(self._on_embed_device)
        self.embed_top_k.valueChanged.connect(self._on_embed_top_k)

    def _on_embeddings(self) -> None:
        self._config.memory.embeddings_enabled = self.embeddings_enabled.isChecked()
        self._persist()

    def _on_embed_device(self, value: str) -> None:
        self._config.memory.embeddings_device = value
        self._persist()

    def _on_embed_top_k(self, value: int) -> None:
        self._config.memory.embeddings_top_k = value
        self.embed_top_k_label.setText(str(value))
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from MemoryTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from MemoryTab")
