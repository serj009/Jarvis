"""Tests for the capability catalog used by the Help panel and Help tab."""

from __future__ import annotations

from jarvis.tools.catalogue import local_tool_names, voice_pattern_catalogue
from jarvis.ui.capabilities import (
    CAPABILITY_CATEGORIES,
    all_capabilities,
    search_capabilities,
)


def test_categories_non_empty_and_each_has_examples():
    assert CAPABILITY_CATEGORIES
    for cat in CAPABILITY_CATEGORIES:
        assert cat.capabilities
        for cap in cat.capabilities:
            assert cap.name
            assert cap.description
            assert cap.examples
            for ex in cap.examples:
                assert ex.strip()


def test_all_capabilities_flat_count_matches():
    flat = all_capabilities()
    total = sum(len(c.capabilities) for c in CAPABILITY_CATEGORIES)
    assert len(flat) == total


def test_search_substring_match():
    results = search_capabilities("note")
    assert results
    names = [cap.name.lower() for _cat, cap in results]
    assert any("note" in n for n in names)


def test_search_empty_returns_everything():
    full = search_capabilities("")
    flat = all_capabilities()
    assert len(full) == len(flat)


def test_search_case_insensitive():
    a = search_capabilities("WEATHER")
    b = search_capabilities("weather")
    assert len(a) == len(b)
    assert a == b


def test_search_no_match():
    assert search_capabilities("zzz-not-a-real-thing") == []


# --- linkage to the tool catalogue -----------------------------------
#
# capabilities.py stays hand-written — see its module docstring for why a
# generated catalogue was rejected. What these tests remove is the drift
# that motivated generating it: a Help card describing a capability the
# registry does not have, or a voice-reachable tool nobody documents.


# Tools deliberately absent from the Help panel, with the reason. Keep
# this list short and argued; it is the escape hatch, not the norm.
_UNDOCUMENTED_TOOLS: dict[str, str] = {
    "list_directory": "LLM-only helper, no voice phrasing to document",
    "add_to_startup": "LLM-dispatched startup management",
    "auto_login": "LLM-dispatched vault auto-login",
    "backup_game_saves": "LLM-dispatched game save management",
    "bookmarks": "LLM-initiated browser helper, no distinct voice command",
    "browser_tabs": "LLM-initiated browser helper, no distinct voice command",
    "cancel_timer": "LLM-dispatched timer management",
    "change_vault_password": "LLM-dispatched vault management",
    "check_page_changes": "LLM-initiated page monitoring",
    "clean_temp_files": "LLM-dispatched disk management",
    "close_other_tabs": "Tab management helper, LLM-dispatched",
    "compare_pages": "LLM-initiated page comparison helper",
    "content_reader": "LLM-initiated browser helper, no distinct voice command",
    "count_tabs": "Tab management helper, LLM-dispatched",
    "disk_space": "LLM-dispatched disk management",
    "display_info": "LLM-dispatched display information",
    "download_file": "LLM-dispatched file download",
    "duplicate_tab": "Tab management helper, LLM-dispatched",
    "fill_form": "LLM-initiated form fill helper",
    "find_game_saves": "LLM-dispatched game save management",
    "find_large_folders": "LLM-dispatched disk management",
    "find_tab": "Tab management helper, LLM-dispatched",
    "focus_mode_status": "LLM-dispatched focus mode",
    "full_page_screenshot": "Browser helper for full-page capture",
    "generate_password": "LLM-dispatched password generation",
    "generate_qr": "LLM-dispatched QR code generation",
    "get_latest_news": "LLM-dispatched RSS news reader",
    "heavy_processes": "LLM-dispatched process monitoring",
    "list_known_games": "LLM-dispatched game save management",
    "list_page_monitors": "LLM-initiated page monitoring",
    "list_rss_feeds": "LLM-dispatched RSS management",
    "list_saved_sites": "LLM-dispatched vault management",
    "list_startup": "LLM-dispatched startup management",
    "list_timers": "LLM-dispatched timer management",
    "list_windows": "LLM-dispatched window listing",
    "lock_vault": "LLM-dispatched vault management",
    "media_control": "LLM-dispatched media play/pause/next",
    "my_ip": "LLM-dispatched network info",
    "network_info": "LLM-dispatched network info",
    "package_install": "LLM-dispatched package management",
    "package_search": "LLM-dispatched package management",
    "package_uninstall": "LLM-dispatched package management",
    "power_control": "LLM-dispatched shutdown/restart/sleep",
    "price_lookup": "LLM-initiated price comparison helper",
    "process_info": "LLM-dispatched process inspection",
    "remove_credential": "LLM-dispatched vault management",
    "remove_from_startup": "LLM-dispatched startup management",
    "read_screen_text": "OCR-based screen text extraction, LLM-dispatched",
    "run_command": "LLM-dispatched CLI runner",
    "save_credential": "LLM-dispatched vault management",
    "save_page_knowledge": "LLM-initiated knowledge extraction",
    "schedule_notification": "LLM-dispatched notification",
    "scrape_images": "LLM-initiated image download helper",
    "send_hotkey": "LLM-dispatched hotkey sender",
    "set_brightness": "LLM-dispatched display control",
    "set_timer": "LLM-dispatched timer management",
    "share_to_phone": "LLM-dispatched phone sharing",
    "show_notification": "LLM-dispatched notification",
    "speed_test": "LLM-dispatched network info",
    "start_focus_mode": "LLM-dispatched focus mode",
    "start_page_monitor": "LLM-initiated page monitoring",
    "stop_focus_mode": "LLM-dispatched focus mode",
    "stop_page_monitor": "LLM-initiated page monitoring",
    "subscribe_rss": "LLM-dispatched RSS management",
    "toggle_night_mode": "LLM-dispatched display control",
    "translate_page": "LLM-initiated page translation helper",
    "unlock_vault": "LLM-dispatched vault management",
    "unsubscribe_rss": "LLM-dispatched RSS management",
    "wait_for_process": "LLM-dispatched process monitoring",
    "watch_screen": "Background screen watcher, LLM-dispatched",
    "window_action": "LLM-dispatched window management",
    "youtube_summary": "LLM-initiated YouTube summary helper",
}


def _documented_tools() -> set[str]:
    return {
        name
        for cat in CAPABILITY_CATEGORIES
        for cap in cat.capabilities
        for name in cap.tools
    }


def test_every_capability_names_only_real_tools():
    known = set(local_tool_names())
    for cat in CAPABILITY_CATEGORIES:
        for cap in cat.capabilities:
            for name in cap.tools:
                assert name in known, (
                    f"capability {cap.name!r} claims tool {name!r}, which is "
                    "not in jarvis.tools.catalogue"
                )


def test_every_tool_is_documented_by_some_capability():
    missing = set(local_tool_names()) - _documented_tools() - set(_UNDOCUMENTED_TOOLS)
    assert not missing, (
        f"tools with no Help entry: {sorted(missing)}. Add them to a "
        "Capability's `tools`, or to _UNDOCUMENTED_TOOLS with a reason."
    )


def test_undocumented_allow_list_has_no_stale_entries():
    stale = set(_UNDOCUMENTED_TOOLS) - set(local_tool_names())
    assert not stale, f"_UNDOCUMENTED_TOOLS names tools that no longer exist: {stale}"


def test_every_voice_reachable_tool_is_documented():
    """Stricter than the check above: if a user can *say* something and
    reach a tool, the phrase belongs in Help. A voice pattern with no Help
    card is a feature nobody can discover.

    Tools in _UNDOCUMENTED_TOOLS are exempt — they are voice-reachable
    but intentionally left out of the Help panel (e.g. LLM-dispatched
    tools whose voice pattern triggers a secure UI dialog)."""
    reachable = {name for name, _vp in voice_pattern_catalogue()}
    undocumented = reachable - _documented_tools() - set(_UNDOCUMENTED_TOOLS)
    assert not undocumented, (
        f"tools reachable by voice but absent from Help: {sorted(undocumented)}"
    )


def test_capability_categories_and_order_are_stable():
    """The Help panel is read top to bottom and the command palette groups
    by these names, so reordering or renaming a category is a user-visible
    change that should surface as a deliberate diff here."""
    assert [c.name for c in CAPABILITY_CATEGORIES] == [
        "Getting started",
        "Apps and workspace",
        "Music and media",
        "Web and search",
        "Research",
        "Notes",
        "System",
        "Clipboard and typing",
        "Power-user tools",
    ]
    assert all(c.icon_glyph for c in CAPABILITY_CATEGORIES)
