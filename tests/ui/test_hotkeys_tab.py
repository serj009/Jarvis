"""Tests for the Hotkeys settings tab (jarvis.ui.settings.tabs.hotkeys).

Lives under tests/ui/ so it gets the offscreen qapp fixture from
tests/ui/conftest.py (QT_QPA_PLATFORM=offscreen set before PySide6 import).

Covers Task 6f: the chat binding is exposed in the tab, its disabled
default (None) renders as an empty button, a captured combo is written to
config, and Clear writes None (not "") because HotkeysConfig.chat is
Optional[str] — an empty string would register differently at hotkey time.
"""

from __future__ import annotations

from unittest.mock import patch

from jarvis.core.config import JarvisConfig
from jarvis.ui.settings.tabs.hotkeys import HotkeysTab


def _build_tab(cfg):
    changed: list[int] = []
    # _persist() calls save_config; stub it so the test touches no disk.
    with patch("jarvis.ui.settings.tabs.hotkeys.save_config"):
        tab = HotkeysTab(config=cfg, on_change=lambda: changed.append(1))
    return tab, changed


class TestHotkeysTabChatField:
    def test_chat_field_present(self, qapp):
        cfg = JarvisConfig()
        tab, _ = _build_tab(cfg)
        assert "chat" in tab._capture_buttons

    def test_default_chat_renders_empty_not_none_string(self, qapp):
        """chat defaults to None -> button shows "(none)", never "None"."""
        cfg = JarvisConfig()
        assert cfg.hotkeys.chat is None
        tab, _ = _build_tab(cfg)
        btn = tab._capture_buttons["chat"]
        assert btn.text() == "(none)"
        assert btn.text() != "None"

    def test_capture_persists_chat_binding(self, qapp):
        cfg = JarvisConfig()
        tab, changed = _build_tab(cfg)
        with patch("jarvis.ui.settings.tabs.hotkeys.save_config") as save:
            tab._on_captured("chat", "ctrl+shift+c")
        assert cfg.hotkeys.chat == "ctrl+shift+c"
        assert save.call_count == 1
        assert changed  # on_change fired

    def test_clear_writes_none_not_empty_string(self, qapp):
        """Clear on an Optional[str] field must store None, not "".

        An empty string and None are not interchangeable at registration:
        register_all() treats falsy as disabled, but persisting "" where the
        schema expects Optional[str] is a different on-disk value. The tab
        inspects the annotation and clears to None for chat.
        """
        cfg = JarvisConfig()
        cfg.hotkeys.chat = "ctrl+shift+c"
        tab, _ = _build_tab(cfg)
        with patch("jarvis.ui.settings.tabs.hotkeys.save_config"):
            tab._on_clear("chat")
        assert cfg.hotkeys.chat is None
        assert tab._capture_buttons["chat"].text() == "(none)"
