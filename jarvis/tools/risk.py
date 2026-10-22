"""Risk levels for tool actions (T5.2).

Every tool action carries an inherent risk from "read-only information
retrieval" to "irreversible system mutation". This module defines the
risk taxonomy, maps each built-in tool to its level, and provides the
lookup the registry uses to decide whether a tool call needs user
confirmation.

Design decisions:

- Four levels, deliberately coarse. A finer scale (10-point, per-
  parameter) over-classifies and under-communicates: the user sees a
  dialog or doesn't, and a 10-point rating on a 2-outcome UX is noise.
  The four levels map directly to four UX outcomes:

    READ        → execute immediately, no prompt, minimal log.
    WRITE       → execute immediately, audit-log the action.
    DESTRUCTIVE → require user confirmation before executing.
    SYSTEM      → require user confirmation + elevated warning.

- The ``TOOL_RISK_MAP`` is the single source of truth for built-in risk
  assignments. MCP tools default to WRITE (the server is trusted enough
  to connect to, but every mutation is logged) unless the server config
  sets ``requires_confirmation=True``, in which case the registry
  already treats them as confirmable (see registry.py).

- ``risk_level`` is NOT added to the ``Tool`` protocol. The protocol is
  @runtime_checkable and used by MCP tools that have no risk_level
  attribute. Instead, the registry uses getattr() to read it, defaulting
  to READ for tools that predate this module. The ``requires_confirmation``
  bool remains the authoritative confirmation flag; risk_level is an
  additional signal that the registry consults.

- Backward compatibility: a tool with ``requires_confirmation=True`` and
  no ``risk_level`` still gets prompted. A tool with ``risk_level=DESTRUCTIVE``
  and ``requires_confirmation=False`` gets prompted (the risk level
  overrides the explicit False). This is fail-safe: marking something
  destructive must be sufficient; you shouldn't also have to set a
  separate bool.

Integration:
    from jarvis.tools.risk import RiskLevel, risk_level_for_tool
    level = risk_level_for_tool(tool)
    if level.requires_confirmation:
        ...  # prompt the user
"""

from __future__ import annotations

import enum
from typing import Any


class RiskLevel(enum.IntEnum):
    """Risk classification for tool actions.

    IntEnum so levels are naturally ordered: ``DESTRUCTIVE > WRITE`` is True.
    The numeric values are spaced for future insertion if needed."""

    READ = 10
    WRITE = 20
    DESTRUCTIVE = 30
    SYSTEM = 40

    @property
    def requires_confirmation(self) -> bool:
        """Whether this risk level should trigger a confirmation prompt."""
        return self >= RiskLevel.DESTRUCTIVE

    @property
    def label(self) -> str:
        """Human-readable label for UI display."""
        return {
            RiskLevel.READ: "Read-only",
            RiskLevel.WRITE: "Write",
            RiskLevel.DESTRUCTIVE: "Destructive",
            RiskLevel.SYSTEM: "System",
        }.get(self, "Unknown")

    @property
    def description(self) -> str:
        """One-sentence explanation for the settings UI."""
        return {
            RiskLevel.READ: "Retrieves information without modifying anything.",
            RiskLevel.WRITE: "Creates or modifies files/settings.",
            RiskLevel.DESTRUCTIVE: (
                "Deletes or irreversibly changes data. "
                "Requires user confirmation."
            ),
            RiskLevel.SYSTEM: (
                "Executes system-level operations (shell commands, "
                "process control). Requires user confirmation."
            ),
        }.get(self, "")


# ---------------------------------------------------------------------------
# Built-in tool → risk-level mapping
# ---------------------------------------------------------------------------
# This is the single source of truth for which risk level each built-in
# tool carries. Tools not listed here default to READ (safe default).
#
# Ordering matches jarvis/tools/catalogue.py for easy cross-reference.

TOOL_RISK_MAP: dict[str, RiskLevel] = {
    # -- read-only tools (no confirmation, no audit) -----------------------
    "clipboard": RiskLevel.READ,
    "list_directory": RiskLevel.READ,
    "get_time": RiskLevel.READ,
    # Browser tools (Phase 7)
    # Dictation — the tool itself just toggles a flag; actual typing
    # is done via type_dictation_text() which bypasses the registry.
    "dictation": RiskLevel.READ,
    "browser_tabs": RiskLevel.WRITE,
    "content_reader": RiskLevel.READ,
    "bookmarks": RiskLevel.WRITE,
    "media_control": RiskLevel.WRITE,
    "mouse_click": RiskLevel.WRITE,
    "mouse_move": RiskLevel.WRITE,
    "mouse_scroll": RiskLevel.WRITE,
    "mouse_drag": RiskLevel.WRITE,
    "mouse_position": RiskLevel.READ,
    "save_page_knowledge": RiskLevel.WRITE,
    # Weather / system
    "get_weather": RiskLevel.READ,
    "report_cpu_and_memory_percentages": RiskLevel.READ,
    "screenshot": RiskLevel.READ,
    "see_screen": RiskLevel.READ,
    # UI panels — read-only (show/close panels)
    "show_dashboard": RiskLevel.READ,
    "close_dashboard": RiskLevel.READ,
    "show_clipboard_history": RiskLevel.READ,
    "close_clipboard_history": RiskLevel.READ,
    "open_help": RiskLevel.READ,
    "show_logs": RiskLevel.READ,
    "close_logs": RiskLevel.READ,
    "open_notes": RiskLevel.READ,
    "close_notes": RiskLevel.READ,
    "read_note": RiskLevel.READ,
    # Research — read-only (viewing)
    "research": RiskLevel.READ,
    "close_research": RiskLevel.READ,
    "read_more": RiskLevel.READ,
    "copy_research": RiskLevel.READ,
    # Memory — read
    "search_memory": RiskLevel.READ,
    "memory_status": RiskLevel.READ,
    # Volume — WRITE because it mutates system state, but reversible
    "volume": RiskLevel.WRITE,
    # -- write tools (no confirmation, audit-logged) -----------------------
    "open_app": RiskLevel.WRITE,
    "close_app": RiskLevel.WRITE,
    "open_url": RiskLevel.WRITE,
    "play_youtube_music": RiskLevel.WRITE,
    "launch_steam_game": RiskLevel.WRITE,
    "launch_workspace": RiskLevel.WRITE,
    "take_note": RiskLevel.WRITE,
    "append_to_note": RiskLevel.WRITE,
    "switch_language": RiskLevel.WRITE,
    # Deep research — creates files, network calls
    "deep_research": RiskLevel.WRITE,
    "pause_deep_research": RiskLevel.WRITE,
    "resume_deep_research": RiskLevel.WRITE,
    "close_deep_research": RiskLevel.WRITE,
    "enable_deep_research_ultra": RiskLevel.WRITE,
    "disable_deep_research_ultra": RiskLevel.WRITE,
    # Memory — write
    "remember_fact": RiskLevel.WRITE,
    # -- destructive tools (confirmation required) -------------------------
    "delete_note": RiskLevel.DESTRUCTIVE,
    "delete_deep_research": RiskLevel.DESTRUCTIVE,
    "delete_all_deep_research": RiskLevel.DESTRUCTIVE,
    "clear_clipboard_history": RiskLevel.DESTRUCTIVE,
    # Memory — destructive
    "forget_fact": RiskLevel.DESTRUCTIVE,
    # -- system tools (confirmation + elevated warning) --------------------
    "type_into_active_window": RiskLevel.SYSTEM,
    "lock_screen": RiskLevel.SYSTEM,
    "paste_clipboard_item": RiskLevel.SYSTEM,
    # -- Phase 6: Windows control tools ------------------------------------
    # Window management — reversible, no data loss
    "window_action": RiskLevel.WRITE,
    "list_windows": RiskLevel.READ,
    "snap_layout": RiskLevel.WRITE,
    "minimize_all": RiskLevel.WRITE,
    "restore_all": RiskLevel.WRITE,
    "virtual_desktop": RiskLevel.WRITE,
    # Timers — no system mutation, fully reversible
    "set_timer": RiskLevel.WRITE,
    "cancel_timer": RiskLevel.WRITE,
    "list_timers": RiskLevel.READ,
    # Power control — shutdown/restart are SYSTEM-level (irreversible)
    "power_control": RiskLevel.SYSTEM,
    # CLI runner — whitelisted read-only commands, but shell access = SYSTEM
    "run_command": RiskLevel.SYSTEM,
    # Code runner — read_file is READ, run_script/run_tests execute code = WRITE
    "code_runner": RiskLevel.WRITE,
    # Coding agent — creates/edits files, runs tests, git = WRITE
    "coding_agent": RiskLevel.WRITE,
    # Downloads — creates files, network I/O
    "download_file": RiskLevel.WRITE,
    # Package manager — install/uninstall are destructive
    "package_search": RiskLevel.READ,
    "package_install": RiskLevel.DESTRUCTIVE,
    "package_uninstall": RiskLevel.DESTRUCTIVE,
    # Game saves — find is read-only, backup creates files
    "find_game_saves": RiskLevel.READ,
    "backup_game_saves": RiskLevel.WRITE,
    "list_known_games": RiskLevel.READ,
    # Process monitor — read-only info + blocking wait
    "process_info": RiskLevel.READ,
    "heavy_processes": RiskLevel.READ,
    "wait_for_process": RiskLevel.READ,
    # -- Phase 7 batch 2: browser-power tools ------------------------------
    # Price lookup — read-only web scraping
    "price_lookup": RiskLevel.READ,
    # YouTube summary — read-only (downloads subtitles + LLM call)
    "youtube_summary": RiskLevel.READ,
    # Full page screenshot — read-only capture
    "full_page_screenshot": RiskLevel.READ,
    # Image scraper — downloads files to disk
    "scrape_images": RiskLevel.WRITE,
    # -- Page monitoring / comparison / form fill / translation -------------
    "start_page_monitor": RiskLevel.WRITE,
    "stop_page_monitor": RiskLevel.WRITE,
    "check_page_changes": RiskLevel.READ,
    "list_page_monitors": RiskLevel.READ,
    "compare_pages": RiskLevel.READ,
    "fill_form": RiskLevel.WRITE,
    "translate_page": RiskLevel.READ,
    # -- Phase 6 batch 2: Windows tools ------------------------------------
    # Display manager — brightness/night mode are reversible
    "set_brightness": RiskLevel.WRITE,
    "toggle_night_mode": RiskLevel.WRITE,
    "display_info": RiskLevel.READ,
    # Startup manager — remove is destructive, add is write
    "list_startup": RiskLevel.READ,
    "remove_from_startup": RiskLevel.DESTRUCTIVE,
    "add_to_startup": RiskLevel.WRITE,
    # Network tools — read-only
    "my_ip": RiskLevel.READ,
    "speed_test": RiskLevel.READ,
    "network_info": RiskLevel.READ,
    # Disk cleaner — clean is destructive
    "disk_space": RiskLevel.READ,
    "find_large_folders": RiskLevel.READ,
    "clean_temp_files": RiskLevel.DESTRUCTIVE,
    # Hotkey sender — safe combos READ, dangerous combos gated in code
    "send_hotkey": RiskLevel.WRITE,
    # Notifications — reversible
    "show_notification": RiskLevel.WRITE,
    "schedule_notification": RiskLevel.WRITE,
    # -- Phase 7 batch 3: browser/cross tools ------------------------------
    # Tab manager — reversible browser actions
    "close_other_tabs": RiskLevel.WRITE,
    "count_tabs": RiskLevel.READ,
    "find_tab": RiskLevel.READ,
    "duplicate_tab": RiskLevel.WRITE,
    # Password generator — read-only (generates, optional clipboard)
    "generate_password": RiskLevel.READ,
    # RSS reader — subscriptions stored locally
    "subscribe_rss": RiskLevel.WRITE,
    "unsubscribe_rss": RiskLevel.WRITE,
    "list_rss_feeds": RiskLevel.READ,
    "get_latest_news": RiskLevel.READ,
    # QR share — creates files, opens viewer
    "generate_qr": RiskLevel.WRITE,
    "share_to_phone": RiskLevel.WRITE,
    # Focus mode — modifies behavior/hosts file
    "start_focus_mode": RiskLevel.WRITE,
    "stop_focus_mode": RiskLevel.WRITE,
    "focus_mode_status": RiskLevel.READ,
    # -- Password vault tools -----------------------------------------------
    # Auto-login: WRITE (enters credentials into forms, no data loss)
    "auto_login": RiskLevel.WRITE,
    # Save credential: WRITE (adds data to vault)
    "save_credential": RiskLevel.WRITE,
    # List: read-only (no passwords revealed)
    "list_saved_sites": RiskLevel.READ,
    # Remove: DESTRUCTIVE (permanently deletes credentials)
    "remove_credential": RiskLevel.DESTRUCTIVE,
    # Vault unlock/lock: WRITE (changes vault state)
    "unlock_vault": RiskLevel.WRITE,
    "lock_vault": RiskLevel.WRITE,
    # Change master password: DESTRUCTIVE (re-encrypts entire vault)
    "change_vault_password": RiskLevel.DESTRUCTIVE,
    # -- File manager -------------------------------------------------------
    # find/open/mkdir are WRITE; move/copy/rename are WRITE;
    # delete is DESTRUCTIVE (gated by the dispatch-level risk map below).
    "file_manager": RiskLevel.WRITE,
    "create_file": RiskLevel.WRITE,
    "archive": RiskLevel.WRITE,
    # -- Git tool ---------------------------------------------------------------
    # status/diff/log are read-only; commit and push are WRITE (confirmation
    # is handled per-action inside git_tool.py via _CONFIRM_ACTIONS).
    "git_tool": RiskLevel.WRITE,
    # -- Calculator ---------------------------------------------------------
    "calculator": RiskLevel.READ,
    # -- Converter ----------------------------------------------------------
    "converter": RiskLevel.READ,
    # -- Reminders ----------------------------------------------------------
    "remind_me": RiskLevel.WRITE,
    "remind_me_at": RiskLevel.WRITE,
    "list_reminders": RiskLevel.READ,
    "delete_reminder": RiskLevel.DESTRUCTIVE,
    "remind_recurring": RiskLevel.WRITE,
    "snooze_reminder": RiskLevel.WRITE,
    "edit_reminder": RiskLevel.WRITE,
    "set_initiative": RiskLevel.WRITE,
    "proactive_status": RiskLevel.READ,
    "dismiss_proactive": RiskLevel.WRITE,
    # -- Vision tools (Phase 8) --------------------------------------------
    "watch_screen": RiskLevel.READ,
    "read_screen_text": RiskLevel.READ,
    # -- Repeat last (conversational) --------------------------------------
    "repeat_last": RiskLevel.READ,
    # -- Image generation (Phase 10) ----------------------------------------
    "generate_image": RiskLevel.WRITE,
    # -- Autonomous agent (Phase 11) ----------------------------------------
    "autonomous_agent": RiskLevel.WRITE,
    "open_agent_panel": RiskLevel.READ,
    "close_agent_panel": RiskLevel.READ,
    # -- SkillForge tools ---------------------------------------------------
    # List/detail/history/analyze are read-only
    "list_skills": RiskLevel.READ,
    "skill_detail": RiskLevel.READ,
    "skill_history": RiskLevel.READ,
    "analyze_skills": RiskLevel.READ,
    # Approve/reject/improve are WRITE (modify skill state)
    "approve_skill": RiskLevel.WRITE,
    "reject_skill": RiskLevel.WRITE,
    "improve_skill": RiskLevel.WRITE,
    # Delete and kill switch are DESTRUCTIVE
    "delete_skill": RiskLevel.DESTRUCTIVE,
    "disable_all_skills": RiskLevel.DESTRUCTIVE,
    # -- User-defined protocols (Phase 12) ---------------------------------
    # create/edit/list/enable/disable/run are WRITE (reversible, no data loss)
    "create_protocol": RiskLevel.WRITE,
    "run_protocol": RiskLevel.WRITE,
    "list_protocols": RiskLevel.READ,
    "edit_protocol": RiskLevel.WRITE,
    "enable_protocol": RiskLevel.WRITE,
    "disable_protocol": RiskLevel.WRITE,
    # delete is DESTRUCTIVE (permanently removes protocol)
    "delete_protocol": RiskLevel.DESTRUCTIVE,
}


def risk_level_for_tool(tool: Any) -> RiskLevel:
    """Return the risk level for a tool instance.

    Lookup order:
    1. ``tool.risk_level`` attribute (explicit per-instance/class).
    2. ``TOOL_RISK_MAP[tool.name]`` (the static map above).
    3. ``RiskLevel.READ`` (safe default for unknown tools).

    MCP tools will typically hit case 3 (READ) unless their server
    config sets ``requires_confirmation=True``, which the registry
    already handles independently.
    """
    # 1. Explicit attribute on the tool.
    explicit = getattr(tool, "risk_level", None)
    if isinstance(explicit, RiskLevel):
        return explicit

    # 2. Static map lookup by name.
    name = getattr(tool, "name", None)
    if name and name in TOOL_RISK_MAP:
        return TOOL_RISK_MAP[name]

    # 3. Safe default.
    return RiskLevel.READ


def should_confirm(tool: Any) -> bool:
    """Whether a tool call should require user confirmation.

    Checks BOTH the legacy ``requires_confirmation`` flag AND the risk
    level. Either one being affirmative triggers the prompt — this is
    the backward-compatible fail-safe union.

    This is the single function the registry should call to decide
    whether to prompt. It replaces the raw
    ``getattr(tool, "requires_confirmation", False)`` check.
    """
    # Legacy explicit flag.
    if getattr(tool, "requires_confirmation", False):
        return True
    # Risk-level gate.
    return risk_level_for_tool(tool).requires_confirmation
