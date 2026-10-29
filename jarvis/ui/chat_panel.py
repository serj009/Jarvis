"""Text chat interface for JARVIS — alternative to voice.

Everything that works via voice also works via text.
The user types a command → it goes through the same IntentRouter
as voice transcription → JARVIS responds in chat (+ optionally speaks).

Layout::

    ┌──────────────────────────────────────┐
    │  J.A.R.V.I.S. Chat                  │ ← header + minimize btn
    ├──────────────────────────────────────┤
    │                                      │
    │  User: Открой Chrome                 │ ← message history
    │  JARVIS: Открываю Chrome, sir.       │
    │                                      │
    │  User: Какая погода?                 │
    │  JARVIS: В Зелёной Горе 12°C, ...   │
    │                                      │
    ├──────────────────────────────────────┤
    │  [Type a message...          ] [Send]│ ← input bar
    │  □ Also speak response               │ ← TTS toggle
    └──────────────────────────────────────┘

Threading model
---------------
The panel lives on the Qt main thread.  User text is routed to the audio
loop via the same ``submit_text`` callable used by the command palette
(wired by the composition root in ``app.py``).  Response chunks arrive
back on the Qt thread via cross-thread Signals.

Submission flow:
  1. User types text, presses Enter (or Send button).
  2. ChatPanel calls ``_submit_text(text)`` (injected by app.py).
  3. ``app.py._submit_chat_text`` schedules ``_consume_chat_text`` on
     the audio loop.  That coroutine drives the same
     ``_make_router_adapter`` producer the pipeline and palette use.
  4. Each yielded chunk is emitted back to the Qt thread via
     ``ChatPanel.response_chunk_received`` Signal (marshalled through
     QMetaObject for thread safety).
  5. When the producer is exhausted, ``ChatPanel.response_complete``
     fires with the full assembled text.

Persistent storage
------------------
Messages are persisted to ``%APPDATA%/Jarvis/data/chat_history.json``
via ``ChatStorage``.  History is loaded on construction and trimmed to
the most recent 100 messages.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from jarvis.ui.chat_storage import ChatMessage, ChatStorage
from jarvis.ui.settings.theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BG_CARD,
    BG_INPUT,
    BORDER,
    CARD_RADIUS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

log = logging.getLogger(__name__)

_PANEL_WIDTH = 520
_PANEL_MIN_HEIGHT = 480
_INPUT_MAX_LINES = 5
_HISTORY_LOAD_LIMIT = 100
_COMMAND_HISTORY_MAX = 50

# ---------------------------------------------------------------------------
# Consolidated QSS
# ---------------------------------------------------------------------------

_PANEL_QSS = f"""\
/* --- outer wrapper ------------------------------------------------- */
QWidget#chatInner {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

/* --- header -------------------------------------------------------- */
QLabel#chatTitle {{
    color: {TEXT_SECONDARY};
    font-size: 8pt;
    font-weight: 300;
    letter-spacing: 4px;
    background: transparent;
    border: none;
}}
QPushButton#chatCloseBtn {{
    color: {TEXT_SECONDARY};
    font-size: 16pt;
    background: transparent;
    border: none;
    padding: 0;
}}
QPushButton#chatCloseBtn:hover {{ color: {TEXT_PRIMARY}; }}
QPushButton#chatClearBtn {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    background: transparent;
    border: none;
    padding: 2px 6px;
}}
QPushButton#chatClearBtn:hover {{ color: {TEXT_PRIMARY}; }}

/* --- message bubbles ----------------------------------------------- */
QFrame#msgUser {{
    background: {ACCENT_DIM};
    border: 1px solid {ACCENT};
    border-radius: 10px;
}}
QFrame#msgAssistant {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#msgSystem {{
    background: transparent;
    border: none;
}}
QLabel#msgText {{
    color: {TEXT_PRIMARY};
    font-size: 10pt;
    background: transparent;
    border: none;
}}
QLabel#msgTextSystem {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    font-style: italic;
    background: transparent;
    border: none;
}}
QLabel#msgTimestamp {{
    color: {TEXT_MUTED};
    font-size: 7pt;
    background: transparent;
    border: none;
}}
QPushButton#msgCopyBtn {{
    color: {TEXT_MUTED};
    font-size: 8pt;
    background: transparent;
    border: none;
    padding: 0 4px;
}}
QPushButton#msgCopyBtn:hover {{ color: {TEXT_PRIMARY}; }}

/* --- scroll area --------------------------------------------------- */
QScrollArea#chatScroll {{
    background: {BG};
    border: none;
}}
QWidget#chatScrollContent {{
    background: {BG};
}}

/* --- input area ---------------------------------------------------- */
QPlainTextEdit#chatInput {{
    background: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 10pt;
    font-family: "Segoe UI", system-ui, sans-serif;
}}
QPlainTextEdit#chatInput:focus {{ border-color: {ACCENT}; }}
QPushButton#chatSendBtn {{
    color: {BG};
    background: {ACCENT};
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 10pt;
    font-weight: 500;
    min-height: 36px;
}}
QPushButton#chatSendBtn:hover {{ background: #60a5fa; }}
QPushButton#chatSendBtn:disabled {{
    background: {TEXT_MUTED};
    color: {BG_CARD};
}}

/* --- TTS toggle ---------------------------------------------------- */
QCheckBox#chatTtsToggle {{
    color: {TEXT_SECONDARY};
    font-size: 9pt;
    background: transparent;
    spacing: 6px;
}}
QCheckBox#chatTtsToggle::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG_INPUT};
}}
QCheckBox#chatTtsToggle::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* --- typing indicator ---------------------------------------------- */
QLabel#chatTyping {{
    color: {TEXT_MUTED};
    font-size: 9pt;
    font-style: italic;
    background: transparent;
    border: none;
}}
"""


# ---------------------------------------------------------------------------
# Markdown-to-plain helper (lightweight, no external dep)
# ---------------------------------------------------------------------------


def _md_to_display(text: str) -> str:
    """Minimal Markdown → display text.  Strips code fences, preserves
    newlines, converts **bold** and *italic* markers to plain text
    (QLabel doesn't render HTML sub-tags with QSS easily; the text
    is already readable without decoration)."""
    # Strip triple-backtick code fences (keep content)
    text = re.sub(r"```\w*\n?", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# MessageBubble
# ---------------------------------------------------------------------------


class MessageBubble(QFrame):
    """Visual representation of a single chat message."""

    def __init__(
        self,
        msg: ChatMessage,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._msg = msg

        if msg.role == "system":
            self.setObjectName("msgSystem")
        elif msg.role == "assistant":
            self.setObjectName("msgAssistant")
        else:
            self.setObjectName("msgUser")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        # Text
        text_label = QLabel(_md_to_display(msg.text))
        text_label.setObjectName(
            "msgTextSystem" if msg.role == "system" else "msgText"
        )
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        lay.addWidget(text_label)

        # Bottom row: timestamp + copy button
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        ts_label = QLabel(self._format_time(msg.timestamp))
        ts_label.setObjectName("msgTimestamp")
        bottom.addWidget(ts_label)

        bottom.addStretch(1)

        if msg.role != "system":
            copy_btn = QPushButton("📋")
            copy_btn.setObjectName("msgCopyBtn")
            copy_btn.setToolTip("Copy message")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(lambda: self._copy_text(msg.text))
            copy_btn.setFixedSize(24, 20)
            bottom.addWidget(copy_btn)

        lay.addLayout(bottom)

    @staticmethod
    def _format_time(iso_ts: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_ts)
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return ""

    @staticmethod
    def _copy_text(text: str) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)


# ---------------------------------------------------------------------------
# ChatInput — multi-line text input with Enter/Shift+Enter handling
# ---------------------------------------------------------------------------


class ChatInput(QPlainTextEdit):
    """Custom text input: Enter sends, Shift+Enter inserts newline.
    Up/Down arrows cycle through command history."""

    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setPlaceholderText("Type a message...")
        self.setMaximumHeight(100)
        self.setTabChangesFocus(True)

        self._command_history: list[str] = []
        self._history_index: int = -1
        self._current_draft: str = ""

    def add_to_history(self, text: str) -> None:
        """Push a sent command onto the history stack."""
        if text and (not self._command_history or self._command_history[-1] != text):
            self._command_history.append(text)
            if len(self._command_history) > _COMMAND_HISTORY_MAX:
                self._command_history = self._command_history[-_COMMAND_HISTORY_MAX:]
        self._history_index = -1
        self._current_draft = ""

    @property
    def command_history(self) -> list[str]:
        return list(self._command_history)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()

        # Enter without Shift → submit
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not (mods & Qt.KeyboardModifier.ShiftModifier):
                text = self.toPlainText().strip()
                if text:
                    self.submitted.emit(text)
                    self.add_to_history(text)
                    self.clear()
                return
            # Shift+Enter → insert newline (default behaviour)

        # Up arrow at first line → older command
        if key == Qt.Key.Key_Up and self.textCursor().blockNumber() == 0:
            self._navigate_history(-1)
            return

        # Down arrow at last line → newer command
        if key == Qt.Key.Key_Down:
            cursor = self.textCursor()
            if cursor.blockNumber() == self.document().blockCount() - 1:
                self._navigate_history(1)
                return

        super().keyPressEvent(event)

    def _navigate_history(self, direction: int) -> None:
        """Cycle through command history.  direction: -1 = older, +1 = newer."""
        if not self._command_history:
            return

        if self._history_index == -1:
            self._current_draft = self.toPlainText()

        new_idx = self._history_index - direction  # -1 = go back in list
        if direction == -1:
            # Going older
            if self._history_index == -1:
                new_idx = len(self._command_history) - 1
            else:
                new_idx = self._history_index - 1
            if new_idx < 0:
                return  # already at oldest
        else:
            # Going newer
            if self._history_index == -1:
                return  # already at draft
            new_idx = self._history_index + 1
            if new_idx >= len(self._command_history):
                # Back to current draft
                self._history_index = -1
                self.setPlainText(self._current_draft)
                # Move cursor to end
                cursor = self.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.setTextCursor(cursor)
                return

        self._history_index = new_idx
        self.setPlainText(self._command_history[new_idx])
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)


# ---------------------------------------------------------------------------
# ChatPanel
# ---------------------------------------------------------------------------


class ChatPanel(QWidget):
    """Text chat interface for JARVIS — alternative to voice.

    Constructed by the composition root in ``app.py``.  The
    ``submit_text`` callback is the same pipeline the command palette
    and voice transcription use, so every command that works via voice
    also works here.

    ``on_response_chunk`` and ``on_response_complete`` are cross-thread
    Signals so the audio loop can push response data safely.
    """

    # Cross-thread signals: audio loop → Qt main thread
    response_chunk_received = Signal(str)
    response_complete = Signal(str)

    # Emitted when the panel wants to open (from any thread)
    _sig_open = Signal()

    def __init__(
        self,
        *,
        submit_text: Callable[[str], None] | None = None,
        tts_speak: Callable[[str], None] | None = None,
        storage: ChatStorage | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(_PANEL_WIDTH, _PANEL_MIN_HEIGHT)

        self._submit_text = submit_text
        self._tts_speak = tts_speak
        self._storage = storage or ChatStorage()

        self._is_thinking = False
        self._pending_chunks: list[str] = []

        self._build_ui()
        self._load_history()

        # Wire signals
        self.response_chunk_received.connect(self._on_chunk)
        self.response_complete.connect(self._on_response_done)
        self._sig_open.connect(self._on_open)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        inner.setObjectName("chatInner")
        inner.setStyleSheet(_PANEL_QSS)
        outer.addWidget(inner)

        main_lay = QVBoxLayout(inner)
        main_lay.setContentsMargins(16, 12, 16, 12)
        main_lay.setSpacing(8)

        # --- Header ---
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("J.A.R.V.I.S.  CHAT")
        title.setObjectName("chatTitle")
        header.addWidget(title)
        header.addStretch(1)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("chatClearBtn")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_history)
        header.addWidget(clear_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("chatCloseBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close_panel)
        header.addWidget(close_btn)

        main_lay.addLayout(header)

        # --- Separator ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
        main_lay.addWidget(sep)

        # --- Message area (scroll) ---
        self._scroll = QScrollArea()
        self._scroll.setObjectName("chatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._messages_container = QWidget()
        self._messages_container.setObjectName("chatScrollContent")
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(4, 4, 4, 4)
        self._messages_layout.setSpacing(8)
        self._messages_layout.addStretch(1)

        self._scroll.setWidget(self._messages_container)
        main_lay.addWidget(self._scroll, 1)

        # --- Typing indicator ---
        self._typing_label = QLabel("JARVIS is thinking...")
        self._typing_label.setObjectName("chatTyping")
        self._typing_label.hide()
        main_lay.addWidget(self._typing_label)

        # --- Input area ---
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._input = ChatInput()
        self._input.submitted.connect(self._on_submit)
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("chatSendBtn")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_clicked)
        self._send_btn.setFixedWidth(70)
        input_row.addWidget(self._send_btn, 0, Qt.AlignmentFlag.AlignBottom)

        main_lay.addLayout(input_row)

        # --- TTS toggle ---
        self._tts_toggle = QCheckBox("Also speak response")
        self._tts_toggle.setObjectName("chatTtsToggle")
        self._tts_toggle.setChecked(False)
        main_lay.addWidget(self._tts_toggle)

        # Keep refs for tests
        self._inner = inner
        self._close_btn = close_btn
        self._clear_btn = clear_btn

    # -- public API --------------------------------------------------------

    def open_panel(self) -> None:
        """Thread-safe open.  Can be called from any thread."""
        self._sig_open.emit()

    def close_panel(self) -> None:
        self.hide()

    @property
    def tts_enabled(self) -> bool:
        return self._tts_toggle.isChecked()

    @tts_enabled.setter
    def tts_enabled(self, value: bool) -> None:
        self._tts_toggle.setChecked(value)

    # -- history -----------------------------------------------------------

    def _load_history(self) -> None:
        """Load persisted messages into the scroll area."""
        messages = self._storage.load_history(limit=_HISTORY_LOAD_LIMIT)
        for msg in messages:
            self._add_bubble(msg, persist=False)
        self._scroll_to_bottom()

    def _add_bubble(
        self,
        msg: ChatMessage,
        persist: bool = True,
    ) -> MessageBubble:
        """Add a MessageBubble to the chat area."""
        bubble = MessageBubble(msg)

        # Layout alignment: user → right, assistant → left, system → center
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        if msg.role == "user":
            wrapper.addStretch(1)
            wrapper.addWidget(bubble)
        elif msg.role == "system":
            wrapper.addStretch(1)
            wrapper.addWidget(bubble)
            wrapper.addStretch(1)
        else:
            wrapper.addWidget(bubble)
            wrapper.addStretch(1)

        # Insert before the bottom stretch
        count = self._messages_layout.count()
        self._messages_layout.insertLayout(max(0, count - 1), wrapper)

        if persist:
            self._storage.save_message(msg)

        QTimer.singleShot(50, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self) -> None:
        vbar = self._scroll.verticalScrollBar()
        if vbar is not None:
            vbar.setValue(vbar.maximum())

    def add_system_message(self, text: str) -> None:
        """Add a system-level message (centered, small font)."""
        msg = ChatMessage(role="system", text=text)
        self._add_bubble(msg)

    # -- submission --------------------------------------------------------

    def _on_submit(self, text: str) -> None:
        """Handle submitted text from the input widget."""
        if not text:
            return

        # Add user bubble
        user_msg = ChatMessage(role="user", text=text)
        self._add_bubble(user_msg)
        self._input.clear()

        # Show thinking indicator
        self._set_thinking(True)
        self._pending_chunks.clear()

        # Route through the intent pipeline
        if self._submit_text is not None:
            try:
                self._submit_text(text)
            except Exception:
                log.exception("chat submit failed")
                self._set_thinking(False)
                self.add_system_message("⚠ Failed to process command.")

    def _on_send_clicked(self) -> None:
        """Handle Send button click."""
        text = self._input.toPlainText().strip()
        if text:
            self._input.add_to_history(text)
            self._on_submit(text)
            self._input.clear()

    # -- response handling (Signals from audio loop) -----------------------

    @Slot(str)
    def _on_chunk(self, chunk: str) -> None:
        """Receive a response chunk from the audio loop."""
        self._pending_chunks.append(chunk)

    @Slot(str)
    def _on_response_done(self, full_text: str) -> None:
        """Response generation complete.  Display the full text."""
        self._set_thinking(False)

        if not full_text.strip():
            # Empty response = stop intent or error; no bubble needed
            return

        assistant_msg = ChatMessage(role="assistant", text=full_text.strip())
        self._add_bubble(assistant_msg)

        # Optional TTS
        if self._tts_toggle.isChecked() and self._tts_speak is not None:
            try:
                self._tts_speak(full_text.strip())
            except Exception:
                log.debug("TTS speak from chat failed", exc_info=True)

    def _set_thinking(self, thinking: bool) -> None:
        self._is_thinking = thinking
        self._typing_label.setVisible(thinking)
        self._send_btn.setEnabled(not thinking)

    # -- clear history -----------------------------------------------------

    def _on_clear_history(self) -> None:
        """Clear all messages from the UI and storage."""
        # Remove all bubbles
        while self._messages_layout.count() > 1:  # keep the bottom stretch
            item = self._messages_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                lay = item.layout()
                if w is not None:
                    w.deleteLater()
                elif lay is not None:
                    self._clear_layout(lay)

        self._storage.clear()
        self.add_system_message("Chat history cleared.")

    @staticmethod
    def _clear_layout(layout) -> None:
        """Recursively clear a layout and delete its widgets."""
        while layout.count():
            item = layout.takeAt(0)
            if item is not None:
                w = item.widget()
                sub_lay = item.layout()
                if w is not None:
                    w.deleteLater()
                elif sub_lay is not None:
                    ChatPanel._clear_layout(sub_lay)

    # -- open / positioning ------------------------------------------------

    @Slot()
    def _on_open(self) -> None:
        self._center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        self._scroll_to_bottom()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)


# ---------------------------------------------------------------------------
# Factory for the composition root (optional convenience)
# ---------------------------------------------------------------------------


def make_amplitude_callback():
    """Placeholder — not used by ChatPanel.  Kept for API symmetry."""
    pass
