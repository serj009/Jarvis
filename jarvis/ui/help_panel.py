"""Standalone Commands panel — slide-in overlay wrapping CommandsWidget.

Opened by voice ("help", "что ты умеешь", "допомога") or tray menu.
Visually identical to the Settings → Commands tab: same theme tokens,
same cards, same search + language filter.  The only additions are:

* A thin header bar (title + close button)
* Frameless, always-on-top, translucent-background window flags
* Slide-in / slide-out animation on the right edge of the screen
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui.commands_widget import CommandsWidget
from jarvis.ui.settings.theme import (
    BG,
    BORDER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    apply_theme,
)

log = logging.getLogger(__name__)

_DEFAULT_PANEL_WIDTH = 520
_SIDE_MARGIN = 16
_ANIM_MS = 280


class HelpPanel(QWidget):
    """Slide-in commands reference, visually identical to Settings → Commands."""

    _sig_open = Signal()
    _sig_close = Signal()

    def __init__(
        self,
        *,
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

        self._panel_width = panel_width
        self._anim: QPropertyAnimation | None = None

        self._build_ui()
        apply_theme(self)
        self._position_offscreen()

        self._sig_open.connect(self._on_open)
        self._sig_close.connect(self._on_close)

    # ------------------------------------------------------------------ public API

    def open_panel(self) -> None:
        self._sig_open.emit()

    def close_panel(self) -> None:
        self._sig_close.emit()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        self.setFixedWidth(self._panel_width)
        self.setObjectName("HelpPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Inner container (dark background + left border)
        inner = QWidget()
        inner.setStyleSheet(
            f"QWidget {{ background: {BG}; border-left: 1px solid {BORDER}; }}"
        )
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(24, 20, 24, 20)
        inner_layout.setSpacing(16)

        # -- Header: title + close button ----------------------------------
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("J.A.R.V.I.S. Commands")
        title.setObjectName("pageTitle")
        header.addWidget(title)

        header.addStretch(1)

        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {TEXT_MUTED}; font-size: 18px; "
            f"background: transparent; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; "
            f"background: {BORDER}; }}"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close)
        header.addWidget(close_btn)

        inner_layout.addLayout(header)

        # -- Subtitle ------------------------------------------------------
        subtitle = QLabel(
            "Everything JARVIS can do \u2014 "
            'say \u201cHey Jarvis\u201d first, then any phrase below.'
        )
        subtitle.setObjectName("pageDesc")
        subtitle.setWordWrap(True)
        inner_layout.addWidget(subtitle)

        # -- Shared commands widget (search + cards) -----------------------
        self._commands = CommandsWidget()
        inner_layout.addWidget(self._commands, 1)

        root.addWidget(inner)

    # ------------------------------------------------------------------ slots / geometry

    @Slot()
    def _on_open(self) -> None:
        self._show_panel()
        self._commands.focus_search()

    @Slot()
    def _on_close(self) -> None:
        if self.isVisible():
            self._slide_out()

    def _show_panel(self) -> None:
        self._resize_to_screen()
        self.show()
        self._slide_in()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
        else:
            super().keyPressEvent(event)

    # -- screen geometry helpers -------------------------------------------

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

    # -- animations --------------------------------------------------------

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
