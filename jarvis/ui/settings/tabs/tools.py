"""Tools tab: enable/disable per local tool, manage MCP server entries.

MCP add/edit dialogs write to config.mcp_servers; the MCP client reconciles
connections on ConfigChanged (jarvis/tools/mcp_client.py).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, MCPServerConfig, save_config
from jarvis.tools.catalogue import local_tool_names

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human-readable display names for local tools.
# Keys that don't appear here fall back to title-cased name.
# ---------------------------------------------------------------------------

TOOL_DISPLAY_NAMES: dict[str, str] = {
    "append_to_note": "\U0001f4dd Append to Note",
    "calculator": "\U0001f9ee Calculator",
    "clear_clipboard_history": "\U0001f4cb Clear Clipboard History",
    "clipboard": "\U0001f4cb Clipboard",
    "close_app": "\u274c Close App",
    "close_clipboard_history": "\U0001f4cb Close Clipboard History",
    "converter": "\U0001f4b1 Converter",
    "close_dashboard": "\U0001f4ca Close Dashboard",
    "close_deep_research": "\U0001f50d Close Deep Research",
    "close_logs": "\U0001f4c4 Close Logs",
    "close_notes": "\U0001f4dd Close Notes",
    "close_research": "\U0001f50d Close Research",
    "copy_research": "\U0001f4cb Copy Research",
    "deep_research": "\U0001f50e Deep Research",
    "delete_all_deep_research": "\U0001f5d1\ufe0f Delete All Deep Research",
    "delete_deep_research": "\U0001f5d1\ufe0f Delete Deep Research",
    "dictation": "\U0001f399\ufe0f Dictation Mode",
    "delete_note": "\U0001f5d1\ufe0f Delete Note",
    "disable_deep_research_ultra": "\u26a0\ufe0f Disable Ultra Research",
    "enable_deep_research_ultra": "\u26a1 Enable Ultra Research",
    "get_weather": "\u2600\ufe0f Get Weather",
    "launch_steam_game": "\U0001f3ae Launch Steam Game",
    "launch_workspace": "\U0001f4bc Launch Workspace",
    "file_manager": "\U0001f4c1 File Manager",
    "list_directory": "\U0001f4c2 List Directory",
    "lock_screen": "\U0001f512 Lock Screen",
    "open_app": "\U0001f4e6 Open App",
    "open_help": "\u2753 Open Help",
    "open_notes": "\U0001f4dd Open Notes",
    "open_url": "\U0001f310 Open Website",
    "paste_clipboard_item": "\U0001f4cb Paste Clipboard Item",
    "pause_deep_research": "\u23f8\ufe0f Pause Deep Research",
    "play_youtube_music": "\U0001f3b5 Play YouTube Music",
    "read_more": "\U0001f4d6 Read More",
    "read_note": "\U0001f4d6 Read Note",
    "read_screen_text": "\U0001f4d6 Read Screen Text",
    "report_cpu_and_memory_percentages": "\U0001f4ca CPU & Memory Usage",
    "remind_me": "\u23f0 Remind Me (Relative)",
    "remind_me_at": "\u23f0 Remind Me At (Absolute)",
    "list_reminders": "\U0001f4cb List Reminders",
    "delete_reminder": "\u274c Delete Reminder",
    "research": "\U0001f50d Research",
    "resume_deep_research": "\u25b6\ufe0f Resume Deep Research",
    "screenshot": "\U0001f4f8 Screenshot",
    "see_screen": "\U0001f441\ufe0f See Screen",
    "show_clipboard_history": "\U0001f4cb Show Clipboard History",
    "show_dashboard": "\U0001f4ca Show Dashboard",
    "show_logs": "\U0001f4c4 Show Logs",
    "take_note": "\U0001f4dd Take Note",
    "type_into_active_window": "\u2328\ufe0f Type Into Window",
    "volume": "\U0001f50a Volume Control",
    "watch_screen": "\U0001f441\ufe0f Watch Screen",
}


def _display_name(tool_name: str) -> str:
    """Return the human-readable display name for a tool."""
    return TOOL_DISPLAY_NAMES.get(tool_name, tool_name.replace("_", " ").title())


# The two research group boxes below carry their own explanatory copy, so
# which tools belong in them is a LAYOUT decision and stays written down
# here. Everything else is derived, so a new tool gets its checkbox from
# jarvis.tools.catalogue rather than from someone remembering to edit a
# list in the settings UI — that list had drifted from the tool set
# before, which is the whole reason this file is being touched.
# tests/ui/test_settings.py asserts both directions of the split.
_RESEARCH_TOOL_NAMES: tuple[str, ...] = (
    "research",
    "close_research",
    "read_more",
    "copy_research",
)

_DEEP_RESEARCH_TOOL_NAMES: tuple[str, ...] = (
    "deep_research",
    "pause_deep_research",
    "resume_deep_research",
    "close_deep_research",
    "delete_deep_research",
    "delete_all_deep_research",
    "enable_deep_research_ultra",
    "disable_deep_research_ultra",
)

# Every built-in tool that isn't already shown in a research group box.
# Sorted (local_tool_names() sorts) because this renders as a flat
# alphabetical checkbox list the user scans by name.
_LOCAL_TOOL_NAMES: tuple[str, ...] = tuple(
    name
    for name in local_tool_names()
    if name not in set(_RESEARCH_TOOL_NAMES) | set(_DEEP_RESEARCH_TOOL_NAMES)
)


class ToolsTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        tool_names: tuple[str, ...] = _LOCAL_TOOL_NAMES,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_change = on_change
        self._tool_names = tool_names

        self._checkboxes: dict[str, QCheckBox] = {}

        # Research: local Ollama + DuckDuckGo (no cloud API key).
        research_box = QGroupBox("Research (local Ollama + web search)")
        research_form = QFormLayout(research_box)
        hint = QLabel(
            "Summarizes DuckDuckGo results with your Ollama model from "
            "Settings \u2192 Models. Requires Ollama running and internet access."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #808080; font-size: 9pt;")
        research_form.addRow(hint)
        deep_hint = QLabel(
            "Deep research runs multiple sub-topics, saves report.md under "
            "%APPDATA%\\Jarvis\\deep_research, and supports pause/resume in the panel."
        )
        deep_hint.setWordWrap(True)
        deep_hint.setStyleSheet("color: #808080; font-size: 9pt;")
        research_form.addRow(deep_hint)
        for name in _RESEARCH_TOOL_NAMES:
            cb = QCheckBox(_display_name(name))
            cb.setChecked(config.tools.enabled.get(name, True))
            cb.toggled.connect(
                lambda checked, n=name: self._on_tool_toggled(n, checked)
            )
            research_form.addRow(cb)
            self._checkboxes[name] = cb
        for name in _DEEP_RESEARCH_TOOL_NAMES:
            cb = QCheckBox(_display_name(name))
            cb.setChecked(config.tools.enabled.get(name, True))
            cb.toggled.connect(
                lambda checked, n=name: self._on_tool_toggled(n, checked)
            )
            research_form.addRow(cb)
            self._checkboxes[name] = cb

        # Deep research tuning (planner / worker / depth / breadth / etc.)
        deep_box = QGroupBox("Deep research \u2014 planner + worker")
        deep_form = QFormLayout(deep_box)
        deep_explain = QLabel(
            "Planner model designs the research (sub-questions, query "
            "expansion, gap-fill, synthesis). Worker model does grunt "
            "extraction. Leave blank to use your main Models tab model."
        )
        deep_explain.setWordWrap(True)
        deep_explain.setStyleSheet("color: #808080; font-size: 9pt;")
        deep_form.addRow(deep_explain)

        self.research_planner_model = QLineEdit(config.research.planner_model)
        self.research_planner_model.setPlaceholderText(
            "e.g. qwen2.5:7b-instruct (blank = main model)"
        )
        deep_form.addRow("Planner model:", self.research_planner_model)

        self.research_worker_model = QLineEdit(config.research.worker_model)
        self.research_worker_model.setPlaceholderText(
            "e.g. qwen2.5:3b-instruct (blank = main model)"
        )
        deep_form.addRow("Worker model:", self.research_worker_model)

        self.research_depth = QSpinBox()
        self.research_depth.setRange(2, 10)
        self.research_depth.setValue(config.research.depth)
        self.research_depth.setToolTip("Number of sub-questions to investigate")
        deep_form.addRow("Depth (sub-questions):", self.research_depth)

        self.research_breadth = QSpinBox()
        self.research_breadth.setRange(2, 10)
        self.research_breadth.setValue(config.research.breadth)
        self.research_breadth.setToolTip("Sources read per sub-question")
        deep_form.addRow("Breadth (sources):", self.research_breadth)

        self.research_max_page_chars = QSpinBox()
        self.research_max_page_chars.setRange(1000, 20000)
        self.research_max_page_chars.setSingleStep(500)
        self.research_max_page_chars.setValue(config.research.max_page_chars)
        deep_form.addRow("Max chars per page:", self.research_max_page_chars)

        self.research_fetch_pages = QCheckBox("Fetch full page text (slower, better)")
        self.research_fetch_pages.setChecked(config.research.fetch_pages)
        deep_form.addRow(self.research_fetch_pages)

        self.research_gap_fill = QCheckBox("Gap-fill pass (planner reviews & re-searches)")
        self.research_gap_fill.setChecked(config.research.enable_gap_fill)
        deep_form.addRow(self.research_gap_fill)

        ultra_box = QGroupBox("Deep research Ultra (free APIs)")
        ultra_form = QFormLayout(ultra_box)
        ultra_explain = QLabel(
            "Ultra uses Brave Search (optional key), Jina Reader for JS pages, "
            "Groq llama-3.3-70b as planner (optional key), 16k chars/page, and "
            "up to 3 gap-fill passes. Keys: JARVIS_BRAVE_API_KEY, JARVIS_GROQ_API_KEY "
            "or fields below. Falls back to DDG + local Ollama when keys are missing."
        )
        ultra_explain.setWordWrap(True)
        ultra_explain.setStyleSheet("color: #808080; font-size: 9pt;")
        ultra_form.addRow(ultra_explain)

        self.research_ultra_enabled = QCheckBox("Enable Deep Research Ultra mode")
        self.research_ultra_enabled.setChecked(config.research.ultra_enabled)
        ultra_form.addRow(self.research_ultra_enabled)

        self.research_brave_api_key = QLineEdit(config.research.brave_api_key)
        self.research_brave_api_key.setPlaceholderText("or set JARVIS_BRAVE_API_KEY")
        self.research_brave_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        ultra_form.addRow("Brave API key:", self.research_brave_api_key)

        self.research_groq_api_key = QLineEdit(config.research.groq_api_key)
        self.research_groq_api_key.setPlaceholderText("or set JARVIS_GROQ_API_KEY")
        self.research_groq_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        ultra_form.addRow("Groq API key:", self.research_groq_api_key)

        self.research_ultra_planner = QLineEdit(config.research.ultra_planner_model)
        self.research_ultra_planner.setPlaceholderText("groq/llama-3.3-70b-versatile")
        ultra_form.addRow("Ultra planner model:", self.research_ultra_planner)

        self.research_ultra_gap_iters = QSpinBox()
        self.research_ultra_gap_iters.setRange(1, 5)
        self.research_ultra_gap_iters.setValue(config.research.ultra_gap_fill_iterations)
        ultra_form.addRow("Ultra gap-fill passes:", self.research_ultra_gap_iters)

        self.research_ultra_enabled.toggled.connect(self._on_research_ultra_enabled)
        self.research_brave_api_key.editingFinished.connect(self._on_research_brave_key)
        self.research_groq_api_key.editingFinished.connect(self._on_research_groq_key)
        self.research_ultra_planner.editingFinished.connect(self._on_research_ultra_planner)
        self.research_ultra_gap_iters.valueChanged.connect(self._on_research_ultra_gap_iters)

        self.research_planner_model.editingFinished.connect(self._on_research_planner_model)
        self.research_worker_model.editingFinished.connect(self._on_research_worker_model)
        self.research_depth.valueChanged.connect(self._on_research_depth)
        self.research_breadth.valueChanged.connect(self._on_research_breadth)
        self.research_max_page_chars.valueChanged.connect(self._on_research_max_page_chars)
        self.research_fetch_pages.toggled.connect(self._on_research_fetch_pages)
        self.research_gap_fill.toggled.connect(self._on_research_gap_fill)

        # Local tools group.
        tools_box = QGroupBox("Local tools")
        tools_form = QFormLayout(tools_box)
        for name in tool_names:
            cb = QCheckBox(_display_name(name))
            cb.setChecked(config.tools.enabled.get(name, True))
            cb.toggled.connect(
                lambda checked, n=name: self._on_tool_toggled(n, checked)
            )
            tools_form.addRow(cb)
            self._checkboxes[name] = cb

        # MCP servers group.
        mcp_box = QGroupBox("MCP servers")
        mcp_layout = QVBoxLayout(mcp_box)
        self.mcp_list = QListWidget()
        self._repopulate_mcp_list()
        btn_row = QHBoxLayout()
        self.mcp_add = QPushButton("Add\u2026")
        self.mcp_edit = QPushButton("Edit\u2026")
        self.mcp_remove = QPushButton("Remove")
        btn_row.addWidget(self.mcp_add)
        btn_row.addWidget(self.mcp_edit)
        btn_row.addWidget(self.mcp_remove)
        btn_row.addStretch(1)
        mcp_layout.addWidget(self.mcp_list)
        mcp_layout.addLayout(btn_row)
        self.mcp_add.clicked.connect(self._on_mcp_add)
        self.mcp_edit.clicked.connect(self._on_mcp_edit)
        self.mcp_remove.clicked.connect(self._on_mcp_remove)

        root = QVBoxLayout(self)
        root.addWidget(research_box)
        root.addWidget(deep_box)
        root.addWidget(ultra_box)
        root.addWidget(tools_box)
        root.addWidget(mcp_box)
        root.addStretch(1)

    # -- local tools ----------------------------------------------------

    def _on_tool_toggled(self, name: str, checked: bool) -> None:
        self._config.tools.enabled[name] = checked
        self._persist()

    # -- deep research --------------------------------------------------

    def _on_research_planner_model(self) -> None:
        self._config.research.planner_model = self.research_planner_model.text().strip()
        self._persist()

    def _on_research_worker_model(self) -> None:
        self._config.research.worker_model = self.research_worker_model.text().strip()
        self._persist()

    def _on_research_depth(self, value: int) -> None:
        self._config.research.depth = value
        self._persist()

    def _on_research_breadth(self, value: int) -> None:
        self._config.research.breadth = value
        self._persist()

    def _on_research_max_page_chars(self, value: int) -> None:
        self._config.research.max_page_chars = value
        self._persist()

    def _on_research_fetch_pages(self, checked: bool) -> None:
        self._config.research.fetch_pages = checked
        self._persist()

    def _on_research_gap_fill(self, checked: bool) -> None:
        self._config.research.enable_gap_fill = checked
        self._persist()

    def _on_research_ultra_enabled(self, checked: bool) -> None:
        self._config.research.ultra_enabled = checked
        self._persist()

    def _on_research_brave_key(self) -> None:
        self._config.research.brave_api_key = self.research_brave_api_key.text().strip()
        self._persist()

    def _on_research_groq_key(self) -> None:
        self._config.research.groq_api_key = self.research_groq_api_key.text().strip()
        self._persist()

    def _on_research_ultra_planner(self) -> None:
        self._config.research.ultra_planner_model = (
            self.research_ultra_planner.text().strip()
            or "groq/llama-3.3-70b-versatile"
        )
        self._persist()

    def _on_research_ultra_gap_iters(self, value: int) -> None:
        self._config.research.ultra_gap_fill_iterations = value
        self._persist()

    # -- MCP servers ----------------------------------------------------

    def _repopulate_mcp_list(self) -> None:
        self.mcp_list.clear()
        for srv in self._config.mcp_servers:
            text = f"{srv.name}  \u2014  {srv.url}"
            if not srv.enabled:
                text += "  (disabled)"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, srv.name)
            self.mcp_list.addItem(item)

    def _selected_mcp_index(self) -> int | None:
        row = self.mcp_list.currentRow()
        return row if row >= 0 else None

    def _on_mcp_add(self) -> None:
        dlg = _MCPEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.result_config()
        if new is None:
            return
        if any(s.name == new.name for s in self._config.mcp_servers):
            QMessageBox.warning(
                self, "Duplicate", f"A server named {new.name!r} already exists.",
            )
            return
        self._config.mcp_servers = [*self._config.mcp_servers, new]
        self._repopulate_mcp_list()
        self._persist()

    def _on_mcp_edit(self) -> None:
        idx = self._selected_mcp_index()
        if idx is None:
            return
        existing = self._config.mcp_servers[idx]
        dlg = _MCPEditDialog(self, existing=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_config()
        if updated is None:
            return
        servers = list(self._config.mcp_servers)
        servers[idx] = updated
        self._config.mcp_servers = servers
        self._repopulate_mcp_list()
        self._persist()

    def _on_mcp_remove(self) -> None:
        idx = self._selected_mcp_index()
        if idx is None:
            return
        existing = self._config.mcp_servers[idx]
        confirm = QMessageBox.question(
            self, "Remove MCP server",
            f"Remove server {existing.name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        servers = list(self._config.mcp_servers)
        del servers[idx]
        self._config.mcp_servers = servers
        self._repopulate_mcp_list()
        self._persist()

    # -- persist --------------------------------------------------------

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from ToolsTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from ToolsTab")


class _MCPEditDialog(QDialog):
    """Simple add/edit dialog. Returns an MCPServerConfig via result_config
    when accepted. Validates that name + url_or_command are non-empty."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        existing: MCPServerConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Edit MCP server" if existing is not None else "Add MCP server"
        )
        self._existing = existing
        self.name = QLineEdit(existing.name if existing else "")
        self.url = QLineEdit(existing.url if existing else "")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(existing.enabled if existing else True)
        self.token_from_file = QCheckBox(
            "Read auth token from Trayce data dir"
        )
        self.token_from_file.setChecked(
            existing.auth_token_from_file if existing else True
        )
        self.requires_confirmation = QCheckBox(
            "Ask me before running this server's tools"
        )
        self.requires_confirmation.setChecked(
            existing.requires_confirmation if existing else False
        )

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("URL:", self.url)
        form.addRow(self.enabled)
        form.addRow(self.token_from_file)
        form.addRow(self.requires_confirmation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Name required", "Server name cannot be empty.")
            return
        if not self.url.text().strip():
            QMessageBox.warning(self, "URL required", "URL or command cannot be empty.")
            return
        self.accept()

    def result_config(self) -> MCPServerConfig | None:
        try:
            return MCPServerConfig(
                name=self.name.text().strip(),
                url=self.url.text().strip(),
                enabled=self.enabled.isChecked(),
                auth_token_from_file=self.token_from_file.isChecked(),
                requires_confirmation=self.requires_confirmation.isChecked(),
                auth_token=(
                    self._existing.auth_token
                    if self._existing is not None
                    else None
                ),
            )
        except Exception:
            log.exception("MCPServerConfig construction failed")
            return None
