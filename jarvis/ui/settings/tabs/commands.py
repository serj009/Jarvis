"""Commands reference tab — thin wrapper around the shared CommandsWidget.

The heavy lifting (search, language filter, card rendering) lives in
``jarvis.ui.commands_widget.CommandsWidget``, which is also used by the
standalone ``HelpPanel``.  This module adds only the page-level header
that other Settings tabs have ("Commands" / subtitle).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig
from jarvis.ui.commands_widget import CommandsWidget

log = logging.getLogger(__name__)


class CommandsTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        # -- Page header (consistent with other Settings tabs) -------------
        title = QLabel("Commands")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Everything JARVIS can do \u2014 say these after the wake word"
        )
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)

        # -- Shared commands widget ----------------------------------------
        self._widget = CommandsWidget()
        root.addWidget(self._widget, 1)
