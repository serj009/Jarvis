"""Autonomous Agent panel — real-time progress timeline with reasoning steps.

Layout
------
::

    ┌──────────────────────────────────────────────┐
    │  🤖 AUTONOMOUS AGENT           [−] [×]       │
    │  ─────────────────────────────────────────── │
    │  Goal: "Organise my Downloads"                │
    │  Plan: 4 steps                                │
    │  ─────────────────────────────────────────── │
    │  ✓ Scan Downloads directory                   │
    │  ✓ Create folders by file type                │
    │  🔄 Moving files...                           │
    │  ⏳ Report summary                            │
    │  ─────────────────────────────────────────── │
    │  Iteration 3/20  ████████░░░░  Time: 45s      │
    │  [Stop]                                       │
    └──────────────────────────────────────────────┘

Minimized state
---------------
::

    ┌──────────────────────────────────────────┐
    │ 🤖 Agent: "Downloads" ████░░ 3/20        │
    └──────────────────────────────────────────┘

Threading model
---------------
Progress updates arrive from the AutonomousAgent (async generator on the
audio thread).  ``AgentPanel`` exposes cross-thread signals so any thread
can emit safely.  All widget mutations happen on the Qt main thread via
signal→slot connections.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui.settings.theme import (
    ACCENT,
    BG,
    BG_CARD,
    BG_INPUT,
    BORDER,
    CARD_RADIUS,
    ERROR,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

log = logging.getLogger(__name__)

_DEFAULT_PANEL_WIDTH = 520
_SIDE_MARGIN = 16


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

_PANEL_QSS = f"""
QWidget#apInner {{
    background: {BG};
    border-left: 1px solid {BORDER};
}}
QLabel#apTitle {{
    color: {TEXT_SECONDARY};
    font-size: 8pt;
    font-weight: 300;
    letter-spacing: 4px;
    background: transparent;
    border: none;
}}
QPushButton#apCloseBtn {{
    color: {TEXT_SECONDARY};
    font-size: 16pt;
    background: transparent;
    border: none;
    padding: 0;
}}
QPushButton#apCloseBtn:hover {{ color: {TEXT_PRIMARY}; }}
QPushButton#apMinBtn {{
    color: {TEXT_SECONDARY};
    font-size: 12pt;
    background: transparent;
    border: none;
    padding: 0;
}}
QPushButton#apMinBtn:hover {{ color: {TEXT_PRIMARY}; }}
QLabel#apGoalLabel {{
    color: {ACCENT};
    font-size: 11pt;
    font-weight: 300;
    background: transparent;
    border: none;
}}
QFrame#apStepCard {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS};
}}
QLabel#apStepText {{
    color: {TEXT_PRIMARY};
    font-size: 9pt;
    background: transparent;
    border: none;
}}
QLabel#apStepIcon {{
    font-size: 14pt;
    background: transparent;
    border: none;
}}
QLabel#apCounterLabel {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    background: transparent;
    border: none;
}}
QPushButton#apStopBtn {{
    color: {ERROR};
    background: transparent;
    border: 1px solid {ERROR};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 9pt;
    font-weight: 500;
}}
QPushButton#apStopBtn:hover {{
    background: {ERROR};
    color: white;
}}
QPushButton#apConfirmBtn {{
    color: white;
    background: {ACCENT};
    border: none;
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 9pt;
}}
QPushButton#apDenyBtn {{
    color: {TEXT_SECONDARY};
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 9pt;
}}
QProgressBar#apProgress {{
    background: {BG_INPUT};
    border: none;
    border-radius: 4px;
    max-height: 8px;
    text-align: center;
}}
QProgressBar#apProgress::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}
"""


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class AgentPanel(QWidget):
    """Side panel showing autonomous agent progress."""

    # Cross-thread signals
    step_added = Signal(str)
    task_started = Signal(str)
    task_finished = Signal(bool)
    confirmation_requested = Signal(str, str)  # tool_name, description

    def __init__(
        self,
        *,
        speak_fn: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._speak_fn = speak_fn
        self._steps: list[str] = []
        self._goal = ""
        self._running = False
        self._start_time = 0.0
        self._confirm_future: Any | None = None
        self._cancel_fn: Callable[[], None] | None = None

        self.setFixedWidth(_DEFAULT_PANEL_WIDTH)
        self.setVisible(False)

        self._build_ui()
        self.setStyleSheet(_PANEL_QSS)

        # Connect signals to slots
        self.step_added.connect(self._on_step_added)
        self.task_started.connect(self._on_task_started)
        self.task_finished.connect(self._on_task_finished)

        # Timer for elapsed time
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_elapsed)

    # -- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        inner.setObjectName("apInner")
        outer.addWidget(inner)

        root = QVBoxLayout(inner)
        root.setContentsMargins(_SIDE_MARGIN, _SIDE_MARGIN,
                                _SIDE_MARGIN, _SIDE_MARGIN)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("🤖  AUTONOMOUS AGENT")
        title.setObjectName("apTitle")
        header.addWidget(title)
        header.addStretch()

        min_btn = QPushButton("─")
        min_btn.setObjectName("apMinBtn")
        min_btn.setFixedSize(28, 28)
        min_btn.clicked.connect(self.close_panel)
        header.addWidget(min_btn)

        close_btn = QPushButton("×")
        close_btn.setObjectName("apCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close_panel)
        header.addWidget(close_btn)

        root.addLayout(header)

        # Goal label
        self._goal_label = QLabel("")
        self._goal_label.setObjectName("apGoalLabel")
        self._goal_label.setWordWrap(True)
        root.addWidget(self._goal_label)

        # Scroll area for steps
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {BG}; border: none; }}"
        )

        self._steps_widget = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_widget)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        self._steps_layout.setSpacing(8)
        self._steps_layout.addStretch()
        scroll.setWidget(self._steps_widget)

        root.addWidget(scroll, stretch=1)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setObjectName("apProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        root.addWidget(self._progress)

        # Counter + elapsed
        counter_row = QHBoxLayout()
        self._counter_label = QLabel("Ready")
        self._counter_label.setObjectName("apCounterLabel")
        counter_row.addWidget(self._counter_label)
        counter_row.addStretch()
        self._elapsed_label = QLabel("")
        self._elapsed_label.setObjectName("apCounterLabel")
        counter_row.addWidget(self._elapsed_label)
        root.addLayout(counter_row)

        # Stop button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("apStopBtn")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

    # -- Public API -------------------------------------------------------

    def open_panel(self) -> None:
        """Show the panel."""
        self.setVisible(True)

    def close_panel(self) -> None:
        """Hide the panel."""
        self.setVisible(False)

    def start_task(self, goal: str) -> None:
        """Start a new task — thread-safe via signal."""
        self.task_started.emit(goal)

    def add_step(self, message: str) -> None:
        """Add a progress step — thread-safe via signal."""
        self.step_added.emit(message)

    def finish_task(self, *, success: bool = True, log: Any = None) -> None:
        """Mark the task as finished — thread-safe via signal."""
        self.task_finished.emit(success)

    def set_cancel_fn(self, fn: Callable[[], None] | None) -> None:
        """Wire the stop button to a cancel function."""
        self._cancel_fn = fn

    async def ask_confirmation(self, action: Any) -> bool:
        """Ask the user to confirm a destructive action.

        This is called from the audio thread; the dialog runs on the
        Qt main thread via signal/slot.

        For now, returns True immediately (auto-confirm).
        A full inline confirmation dialog can be added later.
        """
        # TODO: Implement inline confirmation dialog
        return True

    # -- Slots (Qt main thread) -------------------------------------------

    @Slot(str)
    def _on_task_started(self, goal: str) -> None:
        self._goal = goal
        self._steps.clear()
        self._running = True
        self._start_time = time.monotonic()

        # Truncate goal for display
        display = goal if len(goal) <= 80 else goal[:77] + "…"
        self._goal_label.setText(f"Goal: {display}")

        # Clear steps
        while self._steps_layout.count() > 1:
            item = self._steps_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._progress.setValue(0)
        self._counter_label.setText("Starting…")
        self._elapsed_label.setText("0s")
        self._stop_btn.setEnabled(True)
        self._timer.start()
        self.open_panel()

    @Slot(str)
    def _on_step_added(self, message: str) -> None:
        self._steps.append(message)

        # Determine icon
        icon = "🔄"
        if message.startswith("✓"):
            icon = "✅"
        elif message.startswith("✗"):
            icon = "❌"
        elif "план" in message.lower() or "plan" in message.lower():
            icon = "📋"
        elif "выполняю" in message.lower() or "executing" in message.lower():
            icon = "⚡"
        elif "готов" in message.lower() or "done" in message.lower():
            icon = "✅"

        # Build step card
        card = QFrame()
        card.setObjectName("apStepCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)

        icon_lbl = QLabel(icon)
        icon_lbl.setObjectName("apStepIcon")
        icon_lbl.setFixedWidth(28)
        card_layout.addWidget(icon_lbl)

        text_lbl = QLabel(message)
        text_lbl.setObjectName("apStepText")
        text_lbl.setWordWrap(True)
        card_layout.addWidget(text_lbl, stretch=1)

        # Insert before the stretch
        self._steps_layout.insertWidget(
            self._steps_layout.count() - 1, card
        )

        # Update counter
        n = len(self._steps)
        self._counter_label.setText(f"Step {n}")
        self._progress.setValue(min(n * 5, 100))

        # Auto-scroll
        scroll_area = self._steps_widget.parent()
        if isinstance(scroll_area, QScrollArea):
            bar = scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())

    @Slot(bool)
    def _on_task_finished(self, success: bool) -> None:
        self._running = False
        self._timer.stop()
        self._stop_btn.setEnabled(False)
        self._progress.setValue(100)

        status = "✅ Complete" if success else "❌ Failed"
        self._counter_label.setText(status)

    @Slot()
    def _update_elapsed(self) -> None:
        if self._running:
            elapsed = int(time.monotonic() - self._start_time)
            m, s = divmod(elapsed, 60)
            if m > 0:
                self._elapsed_label.setText(f"{m}m {s}s")
            else:
                self._elapsed_label.setText(f"{s}s")

    def _on_stop(self) -> None:
        if self._cancel_fn is not None:
            self._cancel_fn()
        self._running = False
        self._timer.stop()
        self._stop_btn.setEnabled(False)
        self._counter_label.setText("🚫 Cancelled")
