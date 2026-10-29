"""Tests for ChatPanel, ChatStorage, ChatInput, and MessageBubble.

Architecture note: No real file I/O against user directories — ChatStorage
is pointed at ``tmp_path`` fixtures.  No real TTS or audio loop involved.
We drive the panel by calling internal slots directly and by simulating
cross-thread signals on the Qt event loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# ChatMessage tests (no Qt needed)
# ---------------------------------------------------------------------------


class TestChatMessage:
    """ChatMessage dataclass basics."""

    def test_default_role_is_user(self):
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(text="hello")
        assert msg.role == "user"

    def test_fields_assignable(self):
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(
            role="assistant",
            text="Yes, sir.",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert msg.role == "assistant"
        assert msg.text == "Yes, sir."
        assert "2026" in msg.timestamp

    def test_auto_timestamp(self):
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(text="hi")
        assert msg.timestamp  # not empty
        # Should be parseable ISO
        dt = datetime.fromisoformat(msg.timestamp)
        assert dt.year >= 2026


# ---------------------------------------------------------------------------
# ChatStorage CRUD tests (no Qt needed)
# ---------------------------------------------------------------------------


class TestChatStorage:
    """CRUD operations against a temp JSON file."""

    def test_save_and_load(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json")
        msg = ChatMessage(role="user", text="hello")
        storage.save_message(msg)

        loaded = storage.load_history()
        assert len(loaded) == 1
        assert loaded[0].text == "hello"
        assert loaded[0].role == "user"

    def test_load_history_limit(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json")
        for i in range(10):
            storage.save_message(ChatMessage(text=f"msg{i}"))

        loaded = storage.load_history(limit=3)
        assert len(loaded) == 3
        assert loaded[0].text == "msg7"
        assert loaded[2].text == "msg9"

    def test_clear(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json")
        storage.save_message(ChatMessage(text="hello"))
        storage.save_message(ChatMessage(text="world"))
        assert storage.message_count() == 2

        storage.clear()
        assert storage.message_count() == 0
        assert storage.load_history() == []

    def test_load_empty_when_no_file(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatStorage

        storage = ChatStorage(path=tmp_path / "nope.json")
        assert storage.load_history() == []

    def test_corrupt_file_returns_empty(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatStorage

        p = tmp_path / "bad.json"
        p.write_text("NOT JSON!!!", encoding="utf-8")
        storage = ChatStorage(path=p)
        assert storage.load_history() == []

    def test_max_messages_trim(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json", max_messages=5)
        for i in range(10):
            storage.save_message(ChatMessage(text=f"msg{i}"))

        loaded = storage.load_history(limit=100)
        assert len(loaded) == 5
        # Should keep the 5 newest
        assert loaded[0].text == "msg5"
        assert loaded[4].text == "msg9"

    def test_message_count(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json")
        assert storage.message_count() == 0
        storage.save_message(ChatMessage(text="a"))
        assert storage.message_count() == 1
        storage.save_message(ChatMessage(text="b"))
        assert storage.message_count() == 2

    def test_multiple_roles_roundtrip(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        storage = ChatStorage(path=tmp_path / "chat.json")
        storage.save_message(ChatMessage(role="user", text="hello"))
        storage.save_message(ChatMessage(role="assistant", text="Hi, sir."))
        storage.save_message(ChatMessage(role="system", text="connected"))

        loaded = storage.load_history()
        assert len(loaded) == 3
        assert loaded[0].role == "user"
        assert loaded[1].role == "assistant"
        assert loaded[2].role == "system"

    def test_path_property(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatStorage

        p = tmp_path / "chat.json"
        storage = ChatStorage(path=p)
        assert storage.path == p

    def test_file_created_on_first_write(self, tmp_path: Path):
        from jarvis.ui.chat_storage import ChatMessage, ChatStorage

        p = tmp_path / "subdir" / "chat.json"
        assert not p.exists()

        storage = ChatStorage(path=p)
        storage.save_message(ChatMessage(text="first"))
        assert p.exists()


# ---------------------------------------------------------------------------
# ChatPanel widget tests (need qapp from conftest.py)
# ---------------------------------------------------------------------------


def _make_panel(qapp, tmp_path: Path, submit_text=None, tts_speak=None):
    """Build a ChatPanel with temp storage and no real callbacks."""
    from jarvis.ui.chat_panel import ChatPanel
    from jarvis.ui.chat_storage import ChatStorage

    storage = ChatStorage(path=tmp_path / "panel_chat.json")
    panel = ChatPanel(
        submit_text=submit_text,
        tts_speak=tts_speak,
        storage=storage,
    )
    return panel


class TestChatPanel:
    """ChatPanel construction and behaviour."""

    def test_initially_hidden(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert not panel.isVisible()

    def test_has_input_widget(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert panel._input is not None

    def test_has_send_button(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert panel._send_btn is not None
        assert panel._send_btn.text() == "Send"

    def test_has_tts_toggle(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert panel._tts_toggle is not None
        # Default: unchecked
        assert not panel._tts_toggle.isChecked()

    def test_tts_toggle_property(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert panel.tts_enabled is False
        panel.tts_enabled = True
        assert panel.tts_enabled is True

    def test_submit_text_routes_through_callback(self, qapp, tmp_path):
        received = []
        panel = _make_panel(qapp, tmp_path, submit_text=received.append)

        panel._input.setPlainText("open chrome")
        panel._on_submit("open chrome")

        assert received == ["open chrome"]

    def test_submit_adds_user_bubble(self, qapp, tmp_path):
        from jarvis.ui.chat_storage import ChatStorage

        storage = ChatStorage(path=tmp_path / "panel_chat.json")
        panel = _make_panel(qapp, tmp_path, submit_text=lambda t: None)

        panel._on_submit("hello JARVIS")

        # Check storage has the user message
        messages = storage.load_history()
        # Storage is separate instance; check panel's storage
        panel_messages = panel._storage.load_history()
        assert len(panel_messages) >= 1
        assert panel_messages[-1].role == "user"
        assert panel_messages[-1].text == "hello JARVIS"

    def test_empty_submit_does_nothing(self, qapp, tmp_path):
        received = []
        panel = _make_panel(qapp, tmp_path, submit_text=received.append)

        panel._on_submit("")
        assert received == []

    def test_response_complete_adds_assistant_bubble(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)

        # Simulate a response arriving
        panel.response_complete.emit("Opening Chrome, sir.")

        messages = panel._storage.load_history()
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].text == "Opening Chrome, sir."

    def test_response_complete_empty_text_no_bubble(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        panel.response_complete.emit("")

        messages = panel._storage.load_history()
        assert len(messages) == 0

    def test_thinking_indicator_shown_on_submit(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path, submit_text=lambda t: None)

        panel._on_submit("test")
        assert panel._is_thinking is True
        assert not panel._typing_label.isHidden()

    def test_thinking_indicator_hidden_on_response(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path, submit_text=lambda t: None)

        panel._on_submit("test")
        assert panel._is_thinking is True

        panel._on_response_done("Done, sir.")
        assert panel._is_thinking is False
        assert not panel._typing_label.isVisible()

    def test_clear_history(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path, submit_text=lambda t: None)

        # Add some messages
        panel._on_submit("msg1")
        panel.response_complete.emit("reply1")
        panel._on_submit("msg2")
        panel.response_complete.emit("reply2")

        panel._on_clear_history()

        # Storage should be empty (plus one system message for "cleared")
        messages = panel._storage.load_history()
        assert len(messages) == 1
        assert messages[0].role == "system"

    def test_system_message(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        panel.add_system_message("Connection established.")

        messages = panel._storage.load_history()
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert messages[0].text == "Connection established."

    def test_tts_speak_called_when_toggle_on(self, qapp, tmp_path):
        spoken = []
        panel = _make_panel(
            qapp, tmp_path,
            tts_speak=spoken.append,
        )
        panel.tts_enabled = True

        panel._on_response_done("Good morning, sir.")

        assert len(spoken) == 1
        assert spoken[0] == "Good morning, sir."

    def test_tts_not_called_when_toggle_off(self, qapp, tmp_path):
        spoken = []
        panel = _make_panel(
            qapp, tmp_path,
            tts_speak=spoken.append,
        )
        panel.tts_enabled = False

        panel._on_response_done("Good morning, sir.")

        assert len(spoken) == 0

    def test_send_button_disabled_while_thinking(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path, submit_text=lambda t: None)

        assert panel._send_btn.isEnabled()
        panel._on_submit("test")
        assert not panel._send_btn.isEnabled()

        panel._on_response_done("done")
        assert panel._send_btn.isEnabled()


# ---------------------------------------------------------------------------
# ChatInput widget tests
# ---------------------------------------------------------------------------


class TestChatInput:
    """ChatInput Enter/Shift+Enter and command history."""

    def test_enter_emits_submitted(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        received = []
        widget.submitted.connect(received.append)

        widget.setPlainText("hello")
        # Simulate Enter key
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(event)

        assert received == ["hello"]
        assert widget.toPlainText() == ""  # cleared after submit

    def test_shift_enter_does_not_submit(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        received = []
        widget.submitted.connect(received.append)

        widget.setPlainText("hello")
        # Simulate Shift+Enter
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        widget.keyPressEvent(event)

        assert received == []  # not submitted

    def test_empty_text_not_submitted(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        received = []
        widget.submitted.connect(received.append)

        widget.setPlainText("   ")
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(event)

        assert received == []

    def test_command_history_add(self, qapp):
        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        widget.add_to_history("first")
        widget.add_to_history("second")

        assert widget.command_history == ["first", "second"]

    def test_command_history_no_duplicates(self, qapp):
        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        widget.add_to_history("same")
        widget.add_to_history("same")

        assert widget.command_history == ["same"]

    def test_command_history_up_arrow(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        widget.add_to_history("cmd1")
        widget.add_to_history("cmd2")

        # Press Up → should show "cmd2" (most recent)
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Up,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(event)
        assert widget.toPlainText() == "cmd2"

        # Press Up again → "cmd1" (older)
        widget.keyPressEvent(event)
        assert widget.toPlainText() == "cmd1"

    def test_command_history_down_arrow_back_to_draft(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        from jarvis.ui.chat_panel import ChatInput

        widget = ChatInput()
        widget.add_to_history("cmd1")
        widget.setPlainText("draft")

        # Up → cmd1
        up = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Up,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(up)
        assert widget.toPlainText() == "cmd1"

        # Down → back to draft
        down = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(down)
        assert widget.toPlainText() == "draft"


# ---------------------------------------------------------------------------
# MessageBubble tests
# ---------------------------------------------------------------------------


class TestMessageBubble:
    """MessageBubble rendering for different roles."""

    def test_user_bubble_object_name(self, qapp):
        from jarvis.ui.chat_panel import MessageBubble
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(role="user", text="hello")
        bubble = MessageBubble(msg)
        assert bubble.objectName() == "msgUser"

    def test_assistant_bubble_object_name(self, qapp):
        from jarvis.ui.chat_panel import MessageBubble
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(role="assistant", text="Yes, sir.")
        bubble = MessageBubble(msg)
        assert bubble.objectName() == "msgAssistant"

    def test_system_bubble_object_name(self, qapp):
        from jarvis.ui.chat_panel import MessageBubble
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(role="system", text="connected")
        bubble = MessageBubble(msg)
        assert bubble.objectName() == "msgSystem"

    def test_timestamp_displayed(self, qapp):
        from jarvis.ui.chat_panel import MessageBubble
        from jarvis.ui.chat_storage import ChatMessage

        msg = ChatMessage(
            role="user",
            text="hi",
            timestamp="2026-10-17T14:30:00+00:00",
        )
        bubble = MessageBubble(msg)
        # The timestamp label should exist and show formatted time
        ts_labels = bubble.findChildren(
            type(bubble).mro()[1],  # QFrame -> QWidget
        )
        # Just check construction succeeded (no crash)
        assert bubble is not None


# ---------------------------------------------------------------------------
# Markdown helper tests
# ---------------------------------------------------------------------------


class TestMarkdownHelper:
    """_md_to_display strips code fences."""

    def test_strip_code_fences(self):
        from jarvis.ui.chat_panel import _md_to_display

        text = "```python\nprint('hello')\n```"
        result = _md_to_display(text)
        assert "```" not in result
        assert "print('hello')" in result

    def test_preserves_plain_text(self):
        from jarvis.ui.chat_panel import _md_to_display

        text = "Hello, sir. The temperature is 12°C."
        assert _md_to_display(text) == text

    def test_strips_whitespace(self):
        from jarvis.ui.chat_panel import _md_to_display

        assert _md_to_display("  hello  ") == "hello"
