"""Dynamic tool filtering — send only relevant tools to the LLM.

Reduces context usage from ~45 tools (~2500 tokens) to ~10-15 tools
(~600-900 tokens), freeing ~1500 tokens for conversation history.

Strategy: keyword-based classification. Each tool category has trigger
keywords. A user utterance is matched against keywords to select
relevant tool categories. Always-included tools (get_time, get_weather)
are sent regardless.

This is NOT a replacement for pattern-layer dispatch (which is faster
and deterministic). This is a pre-filter for the LLM fallthrough path:
when the pattern layer doesn't match, the LLM needs tools — but not
ALL tools.

Keyword matching is substring-based (``keyword in utterance_lower``),
not regex. This is intentional: substring search is O(n*m) in CPython's
str.__contains__ but with very small n and m (a few hundred chars of
utterance × a few chars per keyword) it completes in microseconds.
Regex compilation overhead would dominate at this scale, and the
keywords are short stems designed for substring hits (e.g. "музык"
matches "музыку", "музыка", "музыкой").
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool categories with trigger keywords
# ---------------------------------------------------------------------------
# Each category maps a semantic domain to the tool names it governs and
# the trigger keywords (substring stems) that activate it.  A tool may
# appear in more than one category — e.g. ``open_notes`` lives in both
# "notes_memory" and "ui_panels" because "open notes" is ambiguous.
#
# Keywords are SHORT STEMS so a single entry covers all inflected forms:
#   "музык" → музыка, музыку, музыкой, музыки …

TOOL_CATEGORIES: dict[str, dict[str, Any]] = {
    "apps": {
        "tools": [
            "open_app", "close_app", "launch_workspace", "open_url",
        ],
        "keywords_en": [
            "open", "close", "launch", "start", "quit", "exit", "run",
            "website", "site", "url", "browse",
        ],
        "keywords_ru": [
            "открой", "закрой", "запусти", "выключи", "включи",
            "сайт", "ссылк",
        ],
        "keywords_uk": [
            "відкрий", "закрий", "запусти", "вимкни", "увімкни",
            "сайт", "посиланн",
        ],
    },
    "browser": {
        "tools": [
            "browser_tabs", "content_reader", "bookmarks", "media_control",
        ],
        "keywords_en": [
            "tab", "new tab", "close tab", "switch tab",
            "back", "forward", "refresh", "bookmark",
            "read page", "page content", "save page",
            "pause", "resume", "next track", "fullscreen",
        ],
        "keywords_ru": [
            "вкладк", "новая вкладк", "закрой вкладк",
            "назад", "вперёд", "обнови",
            "закладк", "запомни страниц",
            "прочитай страниц", "контент страниц",
            "пауза", "продолж", "следующ", "полный экран",
            "трек", "стоп",
        ],
        "keywords_uk": [
            "вкладк", "нова вкладк", "закрий вкладк",
            "назад", "вперед", "оновити",
            "закладк", "запам'ятай сторінк",
            "прочитай сторінк", "контент сторінк",
            "пауза", "продовж", "наступн", "повний екран",
            "трек", "стоп",
        ],
    },
    "media": {
        "tools": [
            "play_youtube_music", "launch_steam_game", "volume",
        ],
        "keywords_en": [
            "play", "music", "song", "game", "steam", "youtube",
            "volume", "loud", "quiet", "mute", "unmute",
        ],
        "keywords_ru": [
            "музык", "песн", "игр", "стим", "ютуб",
            "громкость", "громче", "тише", "звук",
        ],
        "keywords_uk": [
            "музик", "пісн", "гр", "стім", "ютуб",
            "гучніш", "тихіш", "звук",
        ],
    },
    "info": {
        "tools": [
            "get_weather", "get_time", "screenshot", "see_screen",
            "report_cpu_and_memory_percentages",
        ],
        "keywords_en": [
            "weather", "time", "date", "today",
            "screenshot", "screen", "cpu", "memory", "ram",
            "stats", "status", "temperature", "forecast",
        ],
        "keywords_ru": [
            "погод", "времен", "час", "число", "день", "дат",
            "скриншот", "экран", "систем", "загрузк",
            "процессор", "оперативк", "температур",
        ],
        "keywords_uk": [
            "погод", "час", "годин", "числ", "день", "дат",
            "скріншот", "екран", "систем",
            "процесор", "оператив", "температур",
        ],
    },
    "notes_memory": {
        "tools": [
            "take_note", "read_note", "open_notes", "close_notes",
            "delete_note", "append_to_note",
            "remember", "recall", "forget", "memory_status",
        ],
        "keywords_en": [
            "note", "remember", "recall", "forget", "memory",
            "jot", "write down", "memo",
        ],
        "keywords_ru": [
            "замет", "запиш", "запомн", "вспомн", "забуд",
            "памят", "напомн",
        ],
        "keywords_uk": [
            "нотат", "запиш", "запам", "згадай", "забудь",
            "пам'ят", "нагадай",
        ],
    },
    "research": {
        "tools": [
            "research", "close_research", "read_more", "copy_research",
            "deep_research", "pause_deep_research", "resume_deep_research",
            "close_deep_research", "delete_deep_research",
            "delete_all_deep_research",
            "enable_deep_research_ultra", "disable_deep_research_ultra",
        ],
        "keywords_en": [
            "research", "look up", "investigate", "deep",
            "find out", "search",
        ],
        "keywords_ru": [
            "исследу", "найди информац", "глубок", "поиск",
            "найди", "узнай",
        ],
        "keywords_uk": [
            "досліди", "знайди інформац", "глибок", "пошук",
            "знайди", "дізнай",
        ],
    },
    "ui_panels": {
        "tools": [
            "show_dashboard", "close_dashboard",
            "show_clipboard_history", "close_clipboard_history",
            "show_logs", "close_logs",
            "open_help", "open_notes", "close_notes",
            "clear_clipboard_history", "paste_clipboard_item",
        ],
        "keywords_en": [
            "dashboard", "panel", "clipboard", "logs", "log",
            "help", "capabilities", "paste",
        ],
        "keywords_ru": [
            "панел", "дашборд", "буфер", "логи", "лог",
            "помощ", "справк", "вставь",
        ],
        "keywords_uk": [
            "панел", "дашборд", "буфер", "логи", "лог",
            "допомог", "довідк", "встав",
        ],
    },
    "system": {
        "tools": [
            "lock_screen", "type_into_active_window", "switch_language",
            "clipboard", "list_directory",
        ],
        "keywords_en": [
            "lock", "type", "switch language", "clipboard", "copy",
            "files", "directory", "folder", "list files",
        ],
        "keywords_ru": [
            "заблокир", "набер", "переключ язык", "язык",
            "файл", "папк", "каталог", "скопир",
        ],
        "keywords_uk": [
            "заблокуй", "набер", "переключ мов", "мов",
            "файл", "папк", "каталог", "скопіюй",
        ],
    },
    "windows_control": {
        "tools": [
            "window_action", "list_windows",
        ],
        "keywords_en": [
            "window", "minimize", "maximise", "maximize", "focus",
            "snap", "restore", "list windows",
        ],
        "keywords_ru": [
            "окн", "сверни", "разверни", "фокус", "прикрепи",
            "список окон",
        ],
        "keywords_uk": [
            "вікн", "згорни", "розгорни", "фокус", "прикріпи",
            "список вікон",
        ],
    },
    "timers": {
        "tools": [
            "set_timer", "cancel_timer", "list_timers",
        ],
        "keywords_en": [
            "timer", "alarm", "countdown", "remind",
        ],
        "keywords_ru": [
            "таймер", "будильник", "напомн", "обратный отсчёт",
        ],
        "keywords_uk": [
            "таймер", "будильник", "нагадай", "зворотній відлік",
        ],
    },
    "power": {
        "tools": [
            "power_control",
        ],
        "keywords_en": [
            "shutdown", "shut down", "restart", "reboot",
            "sleep", "hibernate", "caffeinate", "don't sleep",
        ],
        "keywords_ru": [
            "выключи", "перезагру", "спящий", "усни",
            "не засыпай", "гибернац",
        ],
        "keywords_uk": [
            "вимкни", "перезавантаж", "сплячий", "засни",
            "не засинай", "гібернац",
        ],
    },
    "cli": {
        "tools": [
            "run_command",
        ],
        "keywords_en": [
            "command", "terminal", "cmd", "ping", "ipconfig",
            "netstat", "tasklist",
        ],
        "keywords_ru": [
            "команд", "терминал", "пинг", "консоль",
        ],
        "keywords_uk": [
            "команд", "термінал", "пінг", "консоль",
        ],
    },
    "downloads": {
        "tools": [
            "download_file",
        ],
        "keywords_en": [
            "download",
        ],
        "keywords_ru": [
            "скачай", "загрузи",
        ],
        "keywords_uk": [
            "скачай", "завантаж",
        ],
    },
    "packages": {
        "tools": [
            "package_search", "package_install", "package_uninstall",
        ],
        "keywords_en": [
            "install", "uninstall", "winget", "package",
        ],
        "keywords_ru": [
            "установи", "удали программ", "пакет",
        ],
        "keywords_uk": [
            "встанови", "видали програм", "пакет",
        ],
    },
    "game_saves": {
        "tools": [
            "find_game_saves", "backup_game_saves", "list_known_games",
        ],
        "keywords_en": [
            "save", "saves", "backup", "game save",
        ],
        "keywords_ru": [
            "сохранени", "бэкап", "сейв",
        ],
        "keywords_uk": [
            "збереженн", "бекап", "сейв",
        ],
    },
    "processes": {
        "tools": [
            "process_info", "heavy_processes", "wait_for_process",
        ],
        "keywords_en": [
            "process", "memory usage", "ram usage", "heavy",
            "wait for", "installer",
        ],
        "keywords_ru": [
            "процесс", "сколько памяти", "жрёт", "подожди",
            "установщик",
        ],
        "keywords_uk": [
            "процес", "скільки пам", "їсть", "зачекай",
            "встановлювач",
        ],
    },
}

# Tools always included regardless of filter (minimal overhead, high utility).
# These are cheap schemas and the model almost always benefits from seeing
# them: get_time for "what day is it", get_weather for "should I take an
# umbrella", open_app for any ambiguous "open X", volume for "too loud".
ALWAYS_INCLUDED: frozenset[str] = frozenset({
    "get_time", "get_weather", "open_app", "volume",
})

# Maximum tools to send (safety cap — even a fully matched superset
# should not exceed this; the model degrades past ~20 tool schemas).
MAX_FILTERED_TOOLS = 20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_tools_for_utterance(
    utterance: str,
    all_tool_schemas: list[dict],
) -> list[dict]:
    """Filter tool schemas to those relevant for the utterance.

    Returns a subset of *all_tool_schemas*.  If no category keywords match
    the utterance, returns ALL tools (safe fallback — never drops tools
    silently when we are unsure).

    Parameters
    ----------
    utterance:
        The raw user transcription (before normalization — we lower-case
        it internally).
    all_tool_schemas:
        The full list of OpenAI-format tool dicts, typically from
        ``ToolRegistry.as_openai_functions()``.

    Returns
    -------
    list[dict]
        A (potentially smaller) list of tool schemas, preserving the
        original dict objects (no copies).
    """
    if not all_tool_schemas:
        return all_tool_schemas

    utterance_lower = utterance.lower()

    matched_tool_names: set[str] = set(ALWAYS_INCLUDED)
    any_category_matched = False

    for category in TOOL_CATEGORIES.values():
        category_hit = False
        for kw_list_key in ("keywords_en", "keywords_ru", "keywords_uk"):
            if category_hit:
                break
            for keyword in category.get(kw_list_key, []):
                if keyword in utterance_lower:
                    matched_tool_names.update(category["tools"])
                    category_hit = True
                    any_category_matched = True
                    break

    # If NO category keywords matched (only ALWAYS_INCLUDED is present),
    # return ALL tools — don't risk filtering out the right tool for an
    # utterance we don't understand.
    if not any_category_matched:
        return all_tool_schemas

    # Filter schemas by matched names.
    filtered = [
        s for s in all_tool_schemas
        if s.get("function", {}).get("name") in matched_tool_names
    ]

    # Safety: if filter is too aggressive (e.g. the registered tool names
    # diverged from the category lists), return all rather than a near-empty
    # set the model can't do anything useful with.
    if len(filtered) < 3:
        log.debug(
            "[tool_filter] only %d tools after filtering for %r; "
            "returning all %d",
            len(filtered), utterance[:80], len(all_tool_schemas),
        )
        return all_tool_schemas

    if len(filtered) < len(all_tool_schemas):
        log.debug(
            "[tool_filter] %d/%d tools for %r",
            len(filtered), len(all_tool_schemas), utterance[:80],
        )

    return filtered[:MAX_FILTERED_TOOLS]
