"""Coding Agent panel — real-time progress, task history sidebar,
inline confirmation dialogs, minimized widget, and notifications.

Layout
------
::

    ┌──────────────┬──────────────────────────────────┐
    │  Task History │  Active Task                      │
    │  (sidebar)    │  ┌─ Header (name, lang, btns) ──┐ │
    │               │  │ Progress Steps (timeline)     │ │
    │  [New] btn    │  │ ...                           │ │
    │  task 1 ✅    │  │ [Confirm Dialog] (inline)     │ │
    │  task 2 🔄    │  │ ...                           │ │
    │  task 3 ❌    │  │ Summary Card                  │ │
    │               │  └─ Input Bar ──────────────────┘ │
    └──────────────┴──────────────────────────────────┘

Minimized state
---------------
::

    ┌──────────────────────────────────────────┐
    │ 🤖 Coding: "News Parser" ████░░ 4/6     │
    └──────────────────────────────────────────┘

Threading model
---------------
Progress updates arrive from the CodingAgent (async generator on the
audio thread).  ``CodingPanel`` exposes cross-thread signals that any
thread can emit safely.  All actual widget mutations happen on the Qt
main thread via signal→slot connections.

Notifications
-------------
On task completion the panel emits a TTS request (via callback) and
attempts a Windows toast notification (``plyer``, soft-dep).  Sound
effects piggyback on Qt's ``QSound`` or ``winsound``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from jarvis.coding.task_storage import CodingTask, TaskStep, TaskStorage
from jarvis.ui.settings.theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BG_CARD,
    BG_INPUT,
    BORDER,
    CARD_RADIUS,
    ERROR,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
    WARNING_DIM,
)

log = logging.getLogger(__name__)

_DEFAULT_PANEL_WIDTH = 820
_SIDEBAR_WIDTH = 210
_SIDE_MARGIN = 16
_ANIM_MS = 300
_CONFIRM_TIMEOUT_S = 15

# ---------------------------------------------------------------------------
# Status → emoji / colour helpers
# ---------------------------------------------------------------------------

_STATUS_EMOJI: dict[str, str] = {
    "done": "✅",
    "running": "🔄",
    "pending": "⏳",
    "error": "❌",
    "failed": "❌",
    "cancelled": "🚫",
}

_STATUS_COLOR: dict[str, str] = {
    "done": SUCCESS,
    "running": ACCENT,
    "pending": TEXT_MUTED,
    "error": ERROR,
    "failed": ERROR,
    "cancelled": TEXT_MUTED,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Consolidated QSS
# ---------------------------------------------------------------------------

_PANEL_QSS = f"""
/* --- outer wrapper ------------------------------------------------- */
QWidget#cpInner {{
    background: {BG};
    border-left: 1px solid {BORDER};
}}

/* --- header title -------------------------------------------------- */
QLabel#cpTitle {{
    color: {TEXT_SECONDARY};
    font-size: 8pt;
    font-weight: 300;
    letter-spacing: 4px;
    background: transparent;
    border: none;
}}

/* --- close button -------------------------------------------------- */
QPushButton#cpCloseBtn {{
    color: {TEXT_SECONDARY};
    font-size: 16pt;
    background: transparent;
    border: none;
    padding: 0;
}}
QPushButton#cpCloseBtn:hover {{ color: {TEXT_PRIMARY}; }}

/* --- minimize button ----------------------------------------------- */
QPushButton#cpMinBtn {{
    color: {TEXT_SECONDARY};
    font-size: 12pt;
    background: transparent;
    border: none;
    padding: 0;
}}
QPushButton#cpMinBtn:hover {{ color: {TEXT_PRIMARY}; }}

/* --- sidebar ------------------------------------------------------- */
QWidget#cpSidebar {{
    background: {BG};
    border-right: 1px solid {BORDER};
}}
QListWidget#cpTaskList {{
    background: {BG};
    border: none;
    outline: 0;
    font-size: 9pt;
}}
QListWidget#cpTaskList::item {{
    background: transparent;
    color: {TEXT_SECONDARY};
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
}}
QListWidget#cpTaskList::item:selected {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border-left: 3px solid {ACCENT};
}}
QListWidget#cpTaskList::item:hover {{
    background: {BG_CARD};
}}

/* --- new-task button ----------------------------------------------- */
QPushButton#cpNewBtn {{
    color: {ACCENT};
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 9pt;
    font-weight: 500;
}}
QPushButton#cpNewBtn:hover {{
    border-color: {ACCENT};
    background: {ACCENT_DIM};
}}

/* --- active header label ------------------------------------------- */
QLabel#cpActiveTitle {{
    color: {ACCENT};
    font-size: 13pt;
    font-weight: 300;
    letter-spacing: 1px;
    background: transparent;
    border: none;
}}
QLabel#cpLangBadge {{
    color: {ACCENT};
    font-size: 8pt;
    font-weight: 500;
    background: {ACCENT_DIM};
    border-radius: 8px;
    padding: 2px 8px;
    border: none;
}}

/* --- step widgets -------------------------------------------------- */
QFrame#cpStepCard {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS};
}}
QLabel#cpStepIcon {{
    font-size: 14pt;
    background: transparent;
    border: none;
}}
QLabel#cpStepTitle {{
    color: {TEXT_PRIMARY};
    font-size: 10pt;
    font-weight: 500;
    background: transparent;
    border: none;
}}
QLabel#cpStepDetail {{
    color: {TEXT_SECONDARY};
    font-size: 9pt;
    background: transparent;
    border: none;
}}
QLabel#cpStepTime {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    background: transparent;
    border: none;
}}
QLabel#cpFileBadge {{
    color: {TEXT_SECONDARY};
    font-size: 8pt;
    background: {ACCENT_DIM};
    border-radius: 6px;
    padding: 2px 6px;
    border: none;
}}
QPlainTextEdit#cpStepOutput {{
    background: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 8pt;
}}

/* --- confirm dialog (inline) --------------------------------------- */
QFrame#cpConfirmFrame {{
    background: {BG_CARD};
    border: 2px solid {WARNING};
    border-radius: {CARD_RADIUS};
}}
QLabel#cpConfirmText {{
    color: {TEXT_PRIMARY};
    font-size: 10pt;
    background: transparent;
    border: none;
}}
QLabel#cpConfirmTimer {{
    color: {WARNING};
    font-size: 9pt;
    background: transparent;
    border: none;
}}
QPushButton#cpConfirmYes {{
    color: {BG};
    background: {SUCCESS};
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 10pt;
    font-weight: 500;
}}
QPushButton#cpConfirmYes:hover {{ background: #34d399; }}
QPushButton#cpConfirmNo {{
    color: {TEXT_PRIMARY};
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 10pt;
}}
QPushButton#cpConfirmNo:hover {{ border-color: {ERROR}; color: {ERROR}; }}

/* --- summary card -------------------------------------------------- */
QFrame#cpSummaryCard {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS};
}}
QLabel#cpSumLabel {{
    color: {TEXT_SECONDARY};
    font-size: 8pt;
    font-weight: 300;
    letter-spacing: 3px;
    background: transparent;
    border: none;
}}
QLabel#cpSumValue {{
    color: {TEXT_PRIMARY};
    font-size: 12pt;
    font-weight: 300;
    background: transparent;
    border: none;
}}
QPushButton#cpOpenFolder {{
    color: {ACCENT};
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 9pt;
}}
QPushButton#cpOpenFolder:hover {{
    border-color: {ACCENT};
    background: {ACCENT_DIM};
}}

/* --- input bar ----------------------------------------------------- */
QLineEdit#cpInput {{
    background: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 10pt;
}}
QLineEdit#cpInput:focus {{ border-color: {ACCENT}; }}
QPushButton#cpStartBtn {{
    color: {BG};
    background: {ACCENT};
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 10pt;
    font-weight: 500;
}}
QPushButton#cpStartBtn:hover {{ background: #60a5fa; }}
QPushButton#cpStartBtn:disabled {{
    background: {TEXT_MUTED};
    color: {BG_CARD};
}}

/* --- minimized bar ------------------------------------------------- */
QFrame#cpMinBar {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS};
}}
QLabel#cpMinLabel {{
    color: {TEXT_PRIMARY};
    font-size: 9pt;
    background: transparent;
    border: none;
}}
QProgressBar#cpMinProgress {{
    background: {BORDER};
    border: none;
    border-radius: 3px;
    text-align: center;
    font-size: 8pt;
    color: {TEXT_PRIMARY};
}}
QProgressBar#cpMinProgress::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}

/* --- timeline connector line --------------------------------------- */
QWidget#cpTimelineConnector {{
    background: {BORDER};
}}

/* --- scroll areas -------------------------------------------------- */
QScrollArea#cpStepsScroll {{
    background: {BG};
    border: none;
}}

/* --- divider ------------------------------------------------------- */
QFrame#cpDivider {{
    background: {BORDER};
    border: none;
}}
"""


def _restyle(w: QWidget) -> None:
    """Force QSS re-evaluation after an objectName change."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ---------------------------------------------------------------------------
# StepWidget — one timeline entry
# ---------------------------------------------------------------------------


class StepWidget(QFrame):
    """Visual representation of a single :class:`TaskStep`."""

    toggle_output = Signal()  # emitted when output expand/collapse is clicked

    def __init__(self, step: TaskStep, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cpStepCard")
        self._step = step
        self._output_visible = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # Row 1: icon + title + timestamp
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self._icon_lbl = QLabel(_STATUS_EMOJI.get(step.status, "⏳"))
        self._icon_lbl.setObjectName("cpStepIcon")
        self._icon_lbl.setFixedWidth(24)
        row1.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title_lbl = QLabel(step.title or "(untitled step)")
        self._title_lbl.setObjectName("cpStepTitle")
        self._title_lbl.setWordWrap(True)
        title_col.addWidget(self._title_lbl)

        if step.detail:
            detail_lbl = QLabel(step.detail)
            detail_lbl.setObjectName("cpStepDetail")
            detail_lbl.setWordWrap(True)
            title_col.addWidget(detail_lbl)

        row1.addLayout(title_col, 1)

        self._time_lbl = QLabel("")
        self._time_lbl.setObjectName("cpStepTime")
        if step.started_at:
            try:
                dt = datetime.fromisoformat(step.started_at)
                self._time_lbl.setText(dt.strftime("%H:%M:%S"))
            except ValueError:
                pass
        row1.addWidget(self._time_lbl, 0, Qt.AlignmentFlag.AlignTop)

        lay.addLayout(row1)

        # Row 2: file badges (if any)
        if step.files:
            badges_row = QHBoxLayout()
            badges_row.setSpacing(4)
            badges_row.setContentsMargins(32, 0, 0, 0)
            for f in step.files[:8]:
                badge = QLabel(os.path.basename(f))
                badge.setObjectName("cpFileBadge")
                badges_row.addWidget(badge)
            badges_row.addStretch(1)
            lay.addLayout(badges_row)

        # Row 3: collapsible output
        self._output_edit = QPlainTextEdit(step.output or "")
        self._output_edit.setReadOnly(True)
        self._output_edit.setObjectName("cpStepOutput")
        self._output_edit.setMaximumHeight(120)
        self._output_edit.setVisible(False)
        lay.addWidget(self._output_edit)

    # -- public helpers ----------------------------------------------------

    def update_step(self, step: TaskStep) -> None:
        self._step = step
        self._icon_lbl.setText(_STATUS_EMOJI.get(step.status, "⏳"))
        self._title_lbl.setText(step.title or "(untitled step)")
        if step.output:
            self._output_edit.setPlainText(step.output)
        if step.started_at:
            try:
                dt = datetime.fromisoformat(step.started_at)
                self._time_lbl.setText(dt.strftime("%H:%M:%S"))
            except ValueError:
                pass

    def set_output_visible(self, visible: bool) -> None:
        self._output_visible = visible
        self._output_edit.setVisible(visible)

    @property
    def has_output(self) -> bool:
        return bool(self._step.output)


# ---------------------------------------------------------------------------
# InlineConfirmDialog — appears inside the timeline
# ---------------------------------------------------------------------------


class InlineConfirmDialog(QFrame):
    """Inline confirmation prompt with countdown timer.

    Emits ``responded(bool)`` once — *True* for yes, *False* for no or
    timeout.  The first response (voice OR button) wins; calling
    ``resolve(bool)`` from the voice thread is safe.
    """

    responded = Signal(bool)

    def __init__(
        self,
        message: str,
        timeout_s: int = _CONFIRM_TIMEOUT_S,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("cpConfirmFrame")
        self._resolved = False
        self._remaining = timeout_s

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self._msg_lbl = QLabel(message)
        self._msg_lbl.setObjectName("cpConfirmText")
        self._msg_lbl.setWordWrap(True)
        lay.addWidget(self._msg_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._timer_lbl = QLabel(f"Auto-deny in {self._remaining}s")
        self._timer_lbl.setObjectName("cpConfirmTimer")
        row.addWidget(self._timer_lbl)
        row.addStretch(1)

        self._no_btn = QPushButton("❌ No")
        self._no_btn.setObjectName("cpConfirmNo")
        self._no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._no_btn.clicked.connect(lambda: self.resolve(False))
        row.addWidget(self._no_btn)

        self._yes_btn = QPushButton("✅ Yes")
        self._yes_btn.setObjectName("cpConfirmYes")
        self._yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._yes_btn.clicked.connect(lambda: self.resolve(True))
        row.addWidget(self._yes_btn)

        lay.addLayout(row)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # -- public ------------------------------------------------------------

    def resolve(self, approved: bool) -> None:
        """Deliver a verdict.  First call wins; subsequent calls are no-ops."""
        if self._resolved:
            return
        self._resolved = True
        self._tick.stop()
        self._yes_btn.setEnabled(False)
        self._no_btn.setEnabled(False)
        self._timer_lbl.setText("Resolved ✓" if approved else "Denied ✗")
        self.responded.emit(approved)

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    # -- countdown ---------------------------------------------------------

    @Slot()
    def _on_tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self.resolve(False)
            return
        self._timer_lbl.setText(f"Auto-deny in {self._remaining}s")


# ---------------------------------------------------------------------------
# SummaryCard — completion stats
# ---------------------------------------------------------------------------


class SummaryCard(QFrame):
    """Completion statistics shown after a task finishes."""

    open_folder_requested = Signal(str)

    def __init__(self, task: CodingTask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cpSummaryCard")
        self._task = task
        summary = task.summary or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel("SUMMARY")
        hdr.setObjectName("cpSumLabel")
        lay.addWidget(hdr)

        grid = QGridLayout()
        grid.setSpacing(8)

        pairs: list[tuple[str, str]] = [
            ("FILES", str(summary.get("files_created", len(task.steps)))),
            ("TESTS", summary.get("tests_passed", "—")),
            ("TIME", summary.get("duration", "—")),
            ("ITERATIONS", str(summary.get("fix_iterations", 0))),
        ]
        for col, (label, value) in enumerate(pairs):
            lbl = QLabel(label)
            lbl.setObjectName("cpSumLabel")
            val = QLabel(str(value))
            val.setObjectName("cpSumValue")
            grid.addWidget(lbl, 0, col)
            grid.addWidget(val, 1, col)

        lay.addLayout(grid)

        if task.workspace_path:
            btn = QPushButton("Open Folder")
            btn.setObjectName("cpOpenFolder")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda: self.open_folder_requested.emit(task.workspace_path)
            )
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)


# ---------------------------------------------------------------------------
# MinimizedWidget — collapsed progress bar
# ---------------------------------------------------------------------------


class MinimizedWidget(QFrame):
    """Compact bar shown when the full panel is collapsed."""

    expand_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cpMinBar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(10)

        self._emoji_lbl = QLabel("🤖")
        self._emoji_lbl.setObjectName("cpMinLabel")
        lay.addWidget(self._emoji_lbl)

        self._text_lbl = QLabel("Coding: —")
        self._text_lbl.setObjectName("cpMinLabel")
        lay.addWidget(self._text_lbl, 1)

        self._progress = QProgressBar()
        self._progress.setObjectName("cpMinProgress")
        self._progress.setFixedWidth(120)
        self._progress.setFixedHeight(12)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        lay.addWidget(self._progress)

        self._count_lbl = QLabel("0/0")
        self._count_lbl.setObjectName("cpMinLabel")
        lay.addWidget(self._count_lbl)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.expand_requested.emit()
        super().mousePressEvent(event)

    def update_from_task(self, task: CodingTask | None) -> None:
        if task is None:
            self._text_lbl.setText("Coding: —")
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            self._count_lbl.setText("0/0")
            return
        name = task.name or "Task"
        done, total = task.progress
        self._text_lbl.setText(f'Coding: "{name}"')
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(done)
        self._count_lbl.setText(f"{done}/{total}")


# ---------------------------------------------------------------------------
# CodingPanel — main widget
# ---------------------------------------------------------------------------


class CodingPanel(QWidget):
    """Full-featured Coding Agent panel with real-time progress,
    task history sidebar, and inline confirmation dialogs.

    Thread-safe public API (callable from any thread):
      * ``sig_task_updated``  — emit with a CodingTask to refresh UI
      * ``sig_confirm_request`` — emit to show an inline confirm dialog
      * ``sig_close`` — emit to close the panel
      * ``open_panel()`` / ``close_panel()`` — show / slide out
    """

    # Cross-thread signals
    sig_task_updated = Signal(object)       # CodingTask
    sig_confirm_request = Signal(str, object)  # message, result_queue
    sig_close = Signal()
    sig_open = Signal()
    task_submitted = Signal(str)            # query text from input bar
    sig_notification = Signal(str, str)     # title, body (for TTS/toast)

    def __init__(
        self,
        *,
        storage: TaskStorage | None = None,
        speak_fn: Callable[[str], Any] | None = None,
        panel_width: int = _DEFAULT_PANEL_WIDTH,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._storage = storage or TaskStorage()
        self._speak_fn = speak_fn
        self._panel_width = panel_width
        self._anim: QPropertyAnimation | None = None
        self._minimized = False
        self._active_task: CodingTask | None = None
        self._step_widgets: list[StepWidget] = []
        self._confirm_widget: InlineConfirmDialog | None = None

        self._build_ui()
        self._position_offscreen()
        self._load_history()

        # Wire cross-thread signals
        self.sig_task_updated.connect(self._on_task_updated)
        self.sig_confirm_request.connect(self._on_confirm_request)
        self.sig_close.connect(self._on_close)
        self.sig_open.connect(self._on_open)
        self.sig_notification.connect(self._on_notification)

    # ================================================================== #
    # Public thread-safe API                                               #
    # ================================================================== #

    def open_panel(self) -> None:
        """Show and slide the panel in.  Safe from any thread."""
        self.sig_open.emit()

    def close_panel(self) -> None:
        """Close the panel.  Safe from any thread."""
        self.sig_close.emit()

    def update_task(self, task: CodingTask) -> None:
        """Push a task update to the UI.  Safe from any thread."""
        self.sig_task_updated.emit(task)

    def request_confirmation(
        self, message: str, result_queue: object
    ) -> None:
        """Show an inline confirm dialog.  Safe from any thread.

        *result_queue* is a ``queue.Queue`` — the dialog puts ``True``
        or ``False`` into it once the user responds.
        """
        self.sig_confirm_request.emit(message, result_queue)

    def notify(self, title: str, body: str) -> None:
        """Fire a notification (TTS + toast).  Safe from any thread."""
        self.sig_notification.emit(title, body)

    @property
    def active_task(self) -> CodingTask | None:
        return self._active_task

    @property
    def minimized(self) -> bool:
        return self._minimized

    # ================================================================== #
    # UI construction                                                      #
    # ================================================================== #

    def _build_ui(self) -> None:
        self.setFixedWidth(self._panel_width)
        self.setObjectName("CodingPanel")
        self.setStyleSheet(_PANEL_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        inner = QWidget()
        inner.setObjectName("cpInner")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        # --- minimized bar (hidden by default) ---
        self._min_widget = MinimizedWidget()
        self._min_widget.expand_requested.connect(self._on_expand)
        self._min_widget.hide()
        inner_layout.addWidget(self._min_widget)

        # --- full panel content (visible by default) ---
        self._full_content = QWidget()
        full_lay = QVBoxLayout(self._full_content)
        full_lay.setContentsMargins(0, 0, 0, 0)
        full_lay.setSpacing(0)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(16, 14, 16, 10)
        hdr.setSpacing(8)
        title_lbl = QLabel("CODING AGENT")
        title_lbl.setObjectName("cpTitle")
        hdr.addWidget(title_lbl)
        hdr.addStretch(1)

        min_btn = QPushButton("—")
        min_btn.setFixedSize(28, 24)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setObjectName("cpMinBtn")
        min_btn.setToolTip("Minimize")
        min_btn.clicked.connect(self._on_minimize)
        hdr.addWidget(min_btn)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setObjectName("cpCloseBtn")
        close_btn.clicked.connect(self._on_close)
        hdr.addWidget(close_btn)
        full_lay.addLayout(hdr)

        # Splitter: sidebar | main area
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(1)

        # --- sidebar ---
        sidebar = QWidget()
        sidebar.setObjectName("cpSidebar")
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(8, 8, 8, 8)
        sb_lay.setSpacing(8)

        self._new_btn = QPushButton("+ New Task")
        self._new_btn.setObjectName("cpNewBtn")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.clicked.connect(self._on_new_task)
        sb_lay.addWidget(self._new_btn)

        self._task_list = QListWidget()
        self._task_list.setObjectName("cpTaskList")
        self._task_list.currentRowChanged.connect(self._on_task_selected)
        sb_lay.addWidget(self._task_list, 1)

        self._splitter.addWidget(sidebar)

        # --- main area ---
        main_area = QWidget()
        ma_lay = QVBoxLayout(main_area)
        ma_lay.setContentsMargins(16, 8, 16, 12)
        ma_lay.setSpacing(10)

        # Active task header
        active_hdr = QHBoxLayout()
        active_hdr.setSpacing(8)
        self._active_title = QLabel("No task selected")
        self._active_title.setObjectName("cpActiveTitle")
        self._active_title.setWordWrap(True)
        active_hdr.addWidget(self._active_title, 1)

        self._lang_badge = QLabel("")
        self._lang_badge.setObjectName("cpLangBadge")
        self._lang_badge.hide()
        active_hdr.addWidget(self._lang_badge, 0, Qt.AlignmentFlag.AlignTop)
        ma_lay.addLayout(active_hdr)

        # Steps scroll area
        self._steps_scroll = QScrollArea()
        self._steps_scroll.setObjectName("cpStepsScroll")
        self._steps_scroll.setWidgetResizable(True)
        self._steps_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._steps_container = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_container)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        self._steps_layout.setSpacing(6)
        self._steps_layout.addStretch(1)
        self._steps_scroll.setWidget(self._steps_container)
        ma_lay.addWidget(self._steps_scroll, 1)

        # Summary card placeholder
        self._summary_container = QVBoxLayout()
        self._summary_container.setContentsMargins(0, 0, 0, 0)
        ma_lay.addLayout(self._summary_container)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setObjectName("cpDivider")
        ma_lay.addWidget(div)

        # Input bar
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setObjectName("cpInput")
        self._input.setPlaceholderText(
            "Describe coding task... (e.g. 'Create Flask API')"
        )
        self._input.returnPressed.connect(self._on_submit)
        input_row.addWidget(self._input, 1)

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("cpStartBtn")
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_submit)
        input_row.addWidget(self._start_btn)
        ma_lay.addLayout(input_row)

        self._splitter.addWidget(main_area)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        full_lay.addWidget(self._splitter, 1)

        inner_layout.addWidget(self._full_content, 1)
        root.addWidget(inner, 1)

    # ================================================================== #
    # Slots (Qt main thread)                                               #
    # ================================================================== #

    @Slot()
    def _on_open(self) -> None:
        self._resize_to_screen()
        self.show()
        self._slide_in()

    @Slot()
    def _on_close(self) -> None:
        if self.isVisible():
            self._slide_out()

    @Slot()
    def _on_minimize(self) -> None:
        self._minimized = True
        self._full_content.hide()
        self._min_widget.show()
        self._min_widget.update_from_task(self._active_task)

    @Slot()
    def _on_expand(self) -> None:
        self._minimized = False
        self._min_widget.hide()
        self._full_content.show()

    @Slot()
    def _on_new_task(self) -> None:
        self._input.setFocus()
        self._input.clear()

    @Slot()
    def _on_submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.task_submitted.emit(text)

    @Slot(int)
    def _on_task_selected(self, row: int) -> None:
        if row < 0:
            return
        tasks = self._storage.load_all()
        if 0 <= row < len(tasks):
            self._display_task(tasks[row])

    @Slot(object)
    def _on_task_updated(self, task: CodingTask) -> None:
        """Handle a task update (possibly from the coding agent thread)."""
        self._storage.save(task)
        self._load_history()
        if self._active_task is None or self._active_task.id == task.id:
            self._display_task(task)
        # Update minimized widget
        if self._minimized:
            self._min_widget.update_from_task(task)
        # Notify on completion
        if task.status in ("done", "failed"):
            done, total = task.progress
            if task.status == "done":
                body = (
                    f"Task '{task.name}' completed, sir. "
                    f"{done} steps done."
                )
            else:
                body = f"Task '{task.name}' failed, sir."
            self.notify(f"Coding: {task.name}", body)

    @Slot(str, object)
    def _on_confirm_request(self, message: str, result_queue: object) -> None:
        """Show an inline confirmation dialog in the timeline."""
        dialog = InlineConfirmDialog(message)
        self._confirm_widget = dialog

        def _on_response(approved: bool) -> None:
            try:
                result_queue.put_nowait(approved)  # type: ignore[union-attr]
            except Exception:
                log.debug("confirm result_queue.put failed", exc_info=True)

        dialog.responded.connect(_on_response)

        # Insert before the stretch at the end
        count = self._steps_layout.count()
        self._steps_layout.insertWidget(max(count - 1, 0), dialog)

        # Scroll to bottom
        QTimer.singleShot(50, self._scroll_to_bottom)

    @Slot(str, str)
    def _on_notification(self, title: str, body: str) -> None:
        """Fire TTS + toast notification."""
        # TTS
        if self._speak_fn is not None:
            try:
                self._speak_fn(body)
            except Exception:
                log.debug("TTS notification failed", exc_info=True)
        # Windows toast (soft dependency)
        try:
            from plyer import notification as plyer_notif

            plyer_notif.notify(
                title=title,
                message=body,
                app_name="Jarvis",
                timeout=5,
            )
        except Exception:
            log.debug("toast notification unavailable", exc_info=True)
        # Sound
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    # ================================================================== #
    # Display helpers                                                      #
    # ================================================================== #

    def _load_history(self) -> None:
        """Reload the sidebar task list from storage."""
        tasks = self._storage.load_all()
        self._task_list.blockSignals(True)
        current_id = self._active_task.id if self._active_task else None
        self._task_list.clear()
        selected_row = -1
        for i, task in enumerate(tasks):
            emoji = _STATUS_EMOJI.get(task.status, "⏳")
            item = QListWidgetItem(f"{emoji}  {task.name or 'Untitled'}")
            self._task_list.addItem(item)
            if task.id == current_id:
                selected_row = i
        if selected_row >= 0:
            self._task_list.setCurrentRow(selected_row)
        self._task_list.blockSignals(False)

    def _display_task(self, task: CodingTask) -> None:
        """Render a task in the main area."""
        self._active_task = task

        # Header
        self._active_title.setText(task.name or "Untitled Task")
        if task.language:
            self._lang_badge.setText(task.language.upper())
            self._lang_badge.show()
        else:
            self._lang_badge.hide()

        # Clear old step widgets
        self._clear_steps()

        # Build step widgets
        for step in task.steps:
            sw = StepWidget(step)
            self._step_widgets.append(sw)
            # Insert before the stretch
            count = self._steps_layout.count()
            self._steps_layout.insertWidget(max(count - 1, 0), sw)

        # Summary card
        self._clear_summary()
        if task.status in ("done", "failed"):
            card = SummaryCard(task)
            card.open_folder_requested.connect(self._open_folder)
            self._summary_container.addWidget(card)

        # Scroll to bottom for running tasks
        if task.status == "running":
            QTimer.singleShot(50, self._scroll_to_bottom)

    def _clear_steps(self) -> None:
        """Remove all step widgets from the timeline."""
        for sw in self._step_widgets:
            self._steps_layout.removeWidget(sw)
            sw.deleteLater()
        self._step_widgets.clear()
        # Also remove any lingering confirm dialogs
        if self._confirm_widget is not None:
            self._steps_layout.removeWidget(self._confirm_widget)
            self._confirm_widget.deleteLater()
            self._confirm_widget = None

    def _clear_summary(self) -> None:
        while self._summary_container.count():
            item = self._summary_container.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _scroll_to_bottom(self) -> None:
        vbar = self._steps_scroll.verticalScrollBar()
        if vbar:
            vbar.setValue(vbar.maximum())

    @staticmethod
    def _open_folder(path: str) -> None:
        """Open a folder in the OS file explorer."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            log.debug("could not open folder %s", path, exc_info=True)

    # ================================================================== #
    # Geometry / animation (same pattern as DashboardPanel)                #
    # ================================================================== #

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
        else:
            super().keyPressEvent(event)

    def _screen_geometry(self):
        screen = QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _resize_to_screen(self) -> None:
        geo = self._screen_geometry()
        if geo is not None:
            self.setFixedHeight(int(geo.height() * 0.85))

    def _panel_target_pos(self) -> QPoint:
        geo = self._screen_geometry()
        if geo is None:
            return QPoint(0, 0)
        x = geo.right() - self._panel_width - _SIDE_MARGIN + 1
        y = geo.top() + (geo.height() - self.height()) // 2
        return QPoint(x, y)

    def _position_offscreen(self) -> None:
        geo = self._screen_geometry()
        if geo is not None:
            self.move(geo.right() + 10, 0)

    def _slide_in(self) -> None:
        target = self._panel_target_pos()
        start = QPoint(
            target.x() + self._panel_width + _SIDE_MARGIN, target.y()
        )
        self.move(start)
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.setStartValue(start)
        anim.setEndValue(target)
        self._anim = anim
        anim.start()

    def _slide_out(self) -> None:
        current = self.pos()
        end = QPoint(
            current.x() + self._panel_width + _SIDE_MARGIN, current.y()
        )

        def _hide() -> None:
            self.hide()
            self._position_offscreen()

        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.InQuad)
        anim.setStartValue(current)
        anim.setEndValue(end)
        anim.finished.connect(_hide)
        self._anim = anim
        anim.start()
