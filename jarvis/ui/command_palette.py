"""Command palette — keyboard-driven fuzzy launcher.

Press the configured hotkey (default ``ctrl+shift+p``). Start typing —
the list filters live against every Jarvis capability's example phrases
and category names. Up/Down to navigate, Enter to submit the highlighted
phrase as if it were a spoken command, Esc to dismiss.

Submission is routed through the audio loop via the injected
``submit_text`` callable (see ``app.py`` for the wiring), so all the
same intent-router patterns and tools fire as for STT input.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui.capabilities import CAPABILITY_CATEGORIES
from jarvis.ui.settings.theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BG_INPUT,
    BORDER,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

log = logging.getLogger(__name__)

_PANEL_WIDTH = 620
_PANEL_HEIGHT = 480

# ---------------------------------------------------------------------------
# Consolidated stylesheet — one source of truth for every visual rule.
# Widget targeting via objectName; no per-widget setStyleSheet calls.
# ---------------------------------------------------------------------------

_PANEL_QSS = f"""
/* -- outer frame --------------------------------------------------- */
QFrame#cpFrame {{
    background: {BG};
    border: 1px solid {ACCENT};
    border-radius: 8px;
}}

/* -- header labels ------------------------------------------------- */
QLabel#cpTitle {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    font-weight: 300;
    letter-spacing: 4px;
    background: transparent;
}}
QLabel#cpHint {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    background: transparent;
}}

/* -- search input -------------------------------------------------- */
QLineEdit#cpInput {{
    color: {TEXT_PRIMARY};
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    padding: 8px 12px;
    font-size: 12pt;
}}
QLineEdit#cpInput:focus {{
    border: 1px solid {ACCENT};
}}

/* -- results list -------------------------------------------------- */
QListWidget#cpList {{
    background: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    font-size: 10pt;
    padding: 2px;
}}
QListWidget#cpList::item {{
    padding: 6px 8px;
}}
QListWidget#cpList::item:selected {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}

/* -- footer -------------------------------------------------------- */
QLabel#cpFooter {{
    color: {TEXT_MUTED};
    font-size: 9pt;
    background: transparent;
}}
"""


@dataclass
class PaletteEntry:
    phrase: str
    capability_name: str
    category_name: str

    @property
    def display(self) -> str:
        return self.phrase

    @property
    def hint(self) -> str:
        return f"{self.category_name} \u00b7 {self.capability_name}"


def _build_entries() -> list[PaletteEntry]:
    out: list[PaletteEntry] = []
    for cat in CAPABILITY_CATEGORIES:
        for cap in cat.capabilities:
            for phrase in cap.examples:
                out.append(
                    PaletteEntry(
                        phrase=phrase,
                        capability_name=cap.name,
                        category_name=cat.name,
                    )
                )
    return out


def _fuzzy_score(query: str, text: str) -> int:
    """Tiny fuzzy ranker: 0 = no match, higher = better.

    - Exact substring is strongest.
    - Subsequence (letters appear in order) is weaker but still matches.
    - Empty query matches everything with neutral score.
    """
    if not query:
        return 1
    q = query.lower()
    t = text.lower()
    if q in t:
        # Earlier hits rank higher.
        return 1000 - t.index(q)
    qi = 0
    matched = 0
    for ch in t:
        if qi < len(q) and ch == q[qi]:
            qi += 1
            matched += 1
    if qi == len(q):
        return 100 + matched
    return 0


class CommandPalette(QWidget):
    """Frameless, centered, modal-looking command launcher."""

    _sig_open = Signal()

    def __init__(
        self,
        *,
        submit_text: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._submit_text = submit_text
        self._entries = _build_entries()

        self._build_ui()
        self._sig_open.connect(self._on_open)
        self.hide()

    # ------------------------------------------------------------ public

    def open_palette(self) -> None:
        self._sig_open.emit()

    def close_palette(self) -> None:
        self.hide()

    # ----------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        self.setFixedSize(_PANEL_WIDTH, _PANEL_HEIGHT)
        self.setStyleSheet(_PANEL_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("cpFrame")
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(14, 14, 14, 14)
        flay.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("COMMAND PALETTE")
        title.setObjectName("cpTitle")
        hdr.addWidget(title)
        hdr.addStretch(1)
        hint = QLabel("Enter: run    Esc: close")
        hint.setObjectName("cpHint")
        hdr.addWidget(hint)
        flay.addLayout(hdr)

        self._input = QLineEdit()
        self._input.setObjectName("cpInput")
        self._input.setPlaceholderText("Type a command or phrase\u2026")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._submit_current)
        flay.addWidget(self._input)

        self._list = QListWidget()
        self._list.setObjectName("cpList")
        self._list.itemActivated.connect(lambda _it: self._submit_current())
        flay.addWidget(self._list, 1)

        footer = QLabel(
            "Tip: anything you can say to Jarvis works here too. "
            "Examples \u2014 \u2018show dashboard\u2019, \u2018open my notes\u2019, "
            "\u2018research black holes\u2019, \u2018play some jazz\u2019."
        )
        footer.setWordWrap(True)
        footer.setObjectName("cpFooter")
        flay.addWidget(footer)

        root.addWidget(frame)
        self._populate("")

    def _populate(self, query: str) -> None:
        scored: list[tuple[int, PaletteEntry]] = []
        for entry in self._entries:
            haystack = f"{entry.phrase}  {entry.capability_name}  {entry.category_name}"
            score = _fuzzy_score(query, haystack)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda t: -t[0])
        self._list.clear()
        if not scored and query:
            li = QListWidgetItem(f"\u21b5  Run anyway: \u201c{query}\u201d")
            li.setData(Qt.ItemDataRole.UserRole, query)
            self._list.addItem(li)
            self._list.setCurrentRow(0)
            return
        for _score, entry in scored[:80]:
            li = QListWidgetItem(f"{entry.display}\n   {entry.hint}")
            li.setData(Qt.ItemDataRole.UserRole, entry.phrase)
            self._list.addItem(li)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    @Slot(str)
    def _on_text_changed(self, text: str) -> None:
        self._populate(text)

    def _submit_current(self) -> None:
        item = self._list.currentItem()
        text = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not text:
            text = self._input.text().strip()
        if not text:
            return
        try:
            self._submit_text(text)
        except Exception:  # noqa: BLE001
            log.exception("command palette submit failed")
        self.hide()

    # ----------------------------------------------------------- events

    @Slot()
    def _on_open(self) -> None:
        self._input.clear()
        self._populate("")
        self._center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        if key == Qt.Key.Key_Down:
            row = min(self._list.count() - 1, self._list.currentRow() + 1)
            if row >= 0:
                self._list.setCurrentRow(row)
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            row = max(0, self._list.currentRow() - 1)
            self._list.setCurrentRow(row)
            event.accept()
            return
        super().keyPressEvent(event)

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.top() + int(geo.height() * 0.25)
        self.move(x, y)
