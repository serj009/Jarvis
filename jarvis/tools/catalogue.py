"""The static catalogue of built-in local tool *classes*.

Why classes and not a live ToolRegistry
---------------------------------------
This is the deliberate design decision in this module, so it is stated
first.

A `ToolRegistry` is an instance populated across two separate phases of
`jarvis/app.py`: `setup_local_tools()` registers the self-contained tools
early (line ~535), and the panel-backed tools — notes, research, deep
research, dashboard, logs, clipboard history, help — are constructed and
registered several hundred lines later, once the Qt panels they call back
into exist. The `IntentRouter` is built *between* those two phases.
Assembling the pattern table from a registry instance would therefore
silently drop every panel tool's voice patterns.

On top of that, ~271 router tests construct an `IntentRouter` with no
registry at all (`registry=None`, the legacy static-`tools=` path). Those
routers must still have a complete pattern table: the pattern layer is
what makes "open spotify" snappy, and it is specified to work
independently of whether the feedback loop has an executor.

Voice patterns and tool names are properties of the tool *class*, not of
a particular wired-up instance, so the class catalogue is the correct
source. `ToolRegistry` remains the source of truth for what is
*executable* — `IntentRouter._try_pattern` still gates every matched
pattern on `registry.get(name)`, so a tool that is disabled or not
registered falls through to the LLM exactly as before.

What this module feeds
----------------------
- `voice_pattern_catalogue()` -> the router's pattern table
  (`jarvis.llm.intent_router._build_patterns`).
- `local_tool_names()`        -> the Settings → Tools enable/disable list
  (`jarvis.ui.settings.tabs.tools`).

Adding a tool to `LOCAL_TOOL_CLASSES` therefore gives it a voice pattern
and a settings checkbox at once. MCP-adapted tools are absent by
construction: they are discovered at runtime, declare no voice patterns,
and are enabled/disabled per server rather than per tool.
"""

from __future__ import annotations

from jarvis.tools.registry import VoicePattern


def _local_tool_classes() -> tuple[type, ...]:
    """Import and return every built-in local tool class.

    Imports live inside the function, matching the idiom already used by
    `jarvis.tools.setup_local_tools`: it keeps `import jarvis.tools` cheap
    and keeps the module import graph acyclic when the router imports this
    catalogue.

    Ordered by module then by declaration, purely for readability — the
    router sorts by `VoicePattern.priority`, never by this order, and the
    settings tab sorts by name. See registry.PRIORITY_* on why ordering is
    explicit rather than positional.
    """
    from jarvis.tools.local.bookmarks import BookmarksTool
    from jarvis.tools.local.browser_tabs import BrowserTabsTool
    from jarvis.tools.local.clipboard import ClipboardTool
    from jarvis.tools.local.clipboard_history_tools import (
        ClearClipboardHistoryTool,
        CloseClipboardHistoryTool,
        PasteClipboardItemTool,
        ShowClipboardHistoryTool,
    )
    from jarvis.tools.local.close_app import CloseAppTool
    from jarvis.tools.local.page_monitor import (
        StartPageMonitorTool, StopPageMonitorTool,
        CheckPageChangesTool, ListPageMonitorsTool,
    )
    from jarvis.tools.local.page_compare import ComparePagesTool
    from jarvis.tools.local.smart_form_fill import SmartFormFillTool
    from jarvis.tools.local.page_translate import TranslatePageTool

    from jarvis.tools.local.content_reader import ContentReaderTool
    from jarvis.tools.local.dashboard_tools import CloseDashboardTool, ShowDashboardTool
    from jarvis.tools.local.deep_research_tools import (
        CloseDeepResearchTool,
        DeepResearchTool,
        DeleteAllDeepResearchTool,
        DeleteDeepResearchTool,
        PauseDeepResearchTool,
        ResumeDeepResearchTool,
    )
    from jarvis.tools.local.deep_research_ultra_tools import (
        DisableDeepResearchUltraTool,
        EnableDeepResearchUltraTool,
    )
    from jarvis.tools.local.files import ListDirectoryTool
    from jarvis.tools.local.help_tools import OpenHelpTool
    from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool
    from jarvis.tools.local.launch_workspace import LaunchWorkspaceTool
    from jarvis.tools.local.lock_screen import LockScreenTool
    from jarvis.tools.local.log_tools import CloseLogsTool, ShowLogsTool
    from jarvis.tools.local.media_control import MediaControlTool
    from jarvis.tools.local.notes_tools import (
        AppendToNoteTool,
        CloseNotesTool,
        DeleteNoteTool,
        OpenNotesTool,
        ReadNoteTool,
        TakeNoteTool,
    )
    from jarvis.tools.local.open_app import OpenAppTool
    from jarvis.tools.local.open_url import OpenUrlTool
    from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
    from jarvis.tools.local.research import (
        CloseResearchTool,
        CopyResearchTool,
        ReadMoreTool,
        ResearchTool,
    )
    from jarvis.tools.local.screenshot import ScreenshotTool
    from jarvis.tools.local.see_screen import SeeScreenTool
    from jarvis.tools.local.system_stats import SystemStatsTool
    from jarvis.tools.local.type_into_active_window import TypeIntoActiveWindowTool
    from jarvis.tools.local.volume import VolumeTool
    from jarvis.tools.local.weather import WeatherTool

    from jarvis.tools.local.web_knowledge import SavePageKnowledgeTool
    # Phase 6: Windows control modules
    from jarvis.tools.local.window_manager import WindowActionTool, ListWindowsTool
    from jarvis.tools.local.timers import SetTimerTool, CancelTimerTool, ListTimersTool
    from jarvis.tools.local.power_control import PowerControlTool
    from jarvis.tools.local.cli_runner import CLIRunnerTool
    from jarvis.tools.local.download_manager import DownloadFileTool
    from jarvis.tools.local.package_manager import (
        PackageSearchTool, PackageInstallTool, PackageUninstallTool,
    )
    from jarvis.tools.local.game_saves import (
        FindGameSavesTool, BackupGameSavesTool, ListKnownGamesTool,
    )
    from jarvis.tools.local.process_monitor import (
        ProcessInfoTool, HeavyProcessesTool, WaitForProcessTool,
    )
    # Phase 7 batch 2: browser-power tools
    from jarvis.tools.local.price_lookup import PriceLookupTool
    from jarvis.tools.local.youtube_summary import YoutubeSummaryTool
    from jarvis.tools.local.full_page_screenshot import FullPageScreenshotTool
    from jarvis.tools.local.image_scraper import ScrapeImagesTool
    # Phase 6 batch 2: Windows tools
    from jarvis.tools.local.display_manager import (
        SetBrightnessTool, ToggleNightModeTool, DisplayInfoTool,
    )
    from jarvis.tools.local.startup_manager import (
        ListStartupTool, RemoveFromStartupTool, AddToStartupTool,
    )
    from jarvis.tools.local.network_tools import (
        MyIpTool, SpeedTestTool, NetworkInfoTool,
    )
    from jarvis.tools.local.disk_cleaner import (
        DiskSpaceTool, FindLargeFoldersTool, CleanTempFilesTool,
    )
    from jarvis.tools.local.hotkey_sender import SendHotkeyTool
    from jarvis.tools.local.win_notifications import (
        ShowNotificationTool, ScheduleNotificationTool,
    )
    # Phase 7 batch 3: browser/cross tools
    from jarvis.tools.local.tab_manager import (
        CloseOtherTabsTool, CountTabsTool, FindTabTool, DuplicateTabTool,
    )
    from jarvis.tools.local.password_gen import GeneratePasswordTool
    from jarvis.tools.local.rss_reader import (
        SubscribeRssTool, UnsubscribeRssTool,
        ListRssFeedsTool, GetLatestNewsTool,
    )
    from jarvis.tools.local.qr_share import GenerateQrTool, ShareToPhoneTool
    from jarvis.tools.local.focus_mode import (
        StartFocusModeTool, StopFocusModeTool, FocusModeStatusTool,
    )
    # Phase 8: Vision tools
    from jarvis.tools.local.watch_screen import WatchScreenTool
    # Password vault tools
    from jarvis.tools.local.auto_login import AutoLoginTool
    from jarvis.tools.local.vault_manager import (
        SaveCredentialTool, ListSavedSitesTool, RemoveCredentialTool,
        UnlockVaultTool, LockVaultTool, ChangeVaultPasswordTool,
    )

    return (
        BookmarksTool,
        BrowserTabsTool,
        ClipboardTool,
        ClearClipboardHistoryTool,
        CloseClipboardHistoryTool,
        PasteClipboardItemTool,
        ShowClipboardHistoryTool,
        CloseAppTool,
        CloseDashboardTool,
        ContentReaderTool,
        ShowDashboardTool,
        CloseDeepResearchTool,
        DeepResearchTool,
        DeleteAllDeepResearchTool,
        DeleteDeepResearchTool,
        PauseDeepResearchTool,
        ResumeDeepResearchTool,
        DisableDeepResearchUltraTool,
        EnableDeepResearchUltraTool,
        ListDirectoryTool,
        OpenHelpTool,
        LaunchSteamGameTool,
        LaunchWorkspaceTool,
        LockScreenTool,
        CloseLogsTool,
        ShowLogsTool,
        AppendToNoteTool,
        CloseNotesTool,
        DeleteNoteTool,
        MediaControlTool,
        OpenNotesTool,
        ReadNoteTool,
        TakeNoteTool,
        OpenAppTool,
        OpenUrlTool,
        PlayYoutubeMusicTool,
        CloseResearchTool,
        CopyResearchTool,
        ReadMoreTool,
        ResearchTool,
        ScreenshotTool,
        SeeScreenTool,
        SystemStatsTool,
        TypeIntoActiveWindowTool,
        VolumeTool,
        WeatherTool,
        SavePageKnowledgeTool,
        # Phase 6: Windows control tools
        WindowActionTool,
        ListWindowsTool,
        SetTimerTool,
        CancelTimerTool,
        ListTimersTool,
        PowerControlTool,
        CLIRunnerTool,
        DownloadFileTool,
        PackageSearchTool,
        PackageInstallTool,
        PackageUninstallTool,
        FindGameSavesTool,
        BackupGameSavesTool,
        ListKnownGamesTool,
        ProcessInfoTool,
        HeavyProcessesTool,
        WaitForProcessTool,
        # Page monitoring / comparison / form fill / translation
        StartPageMonitorTool,
        StopPageMonitorTool,
        CheckPageChangesTool,
        ListPageMonitorsTool,
        ComparePagesTool,
        SmartFormFillTool,
        TranslatePageTool,
        # Phase 7 batch 2: browser-power tools
        PriceLookupTool,
        YoutubeSummaryTool,
        FullPageScreenshotTool,
        ScrapeImagesTool,
        # Phase 6 batch 2: Windows tools
        SetBrightnessTool,
        ToggleNightModeTool,
        DisplayInfoTool,
        ListStartupTool,
        RemoveFromStartupTool,
        AddToStartupTool,
        MyIpTool,
        SpeedTestTool,
        NetworkInfoTool,
        DiskSpaceTool,
        FindLargeFoldersTool,
        CleanTempFilesTool,
        SendHotkeyTool,
        ShowNotificationTool,
        ScheduleNotificationTool,
        # Phase 7 batch 3: browser/cross tools
        CloseOtherTabsTool,
        CountTabsTool,
        FindTabTool,
        DuplicateTabTool,
        GeneratePasswordTool,
        SubscribeRssTool,
        UnsubscribeRssTool,
        ListRssFeedsTool,
        GetLatestNewsTool,
        GenerateQrTool,
        ShareToPhoneTool,
        StartFocusModeTool,
        StopFocusModeTool,
        FocusModeStatusTool,
        # Phase 8: Vision tools
        WatchScreenTool,
        # Password vault tools
        AutoLoginTool,
        SaveCredentialTool,
        ListSavedSitesTool,
        RemoveCredentialTool,
        UnlockVaultTool,
        LockVaultTool,
        ChangeVaultPasswordTool,
    )


def local_tool_classes() -> tuple[type, ...]:
    """Every built-in local tool class. See module docstring."""
    return _local_tool_classes()


def local_tool_names() -> tuple[str, ...]:
    """Sorted names of every built-in local tool.

    Sorted rather than catalogue-ordered because the only consumer — the
    Settings → Tools checkbox list — is a flat alphabetical list a user
    scans by name."""
    return tuple(sorted(cls.name for cls in _local_tool_classes()))


def voice_pattern_catalogue() -> tuple[tuple[str, VoicePattern], ...]:
    """Every declared voice pattern as (tool_name, pattern), sorted by
    `VoicePattern.priority`.

    Ties break on (tool name, declaration index) rather than on catalogue
    position, so the result is a pure function of the declarations and is
    unaffected by import order. In practice the built-in table has no
    ties: every pattern carries a distinct priority precisely because
    first-match-wins makes ordering a behavioural decision.
    """
    entries: list[tuple[int, str, int, str, VoicePattern]] = []
    for cls in _local_tool_classes():
        patterns = getattr(cls, "voice_patterns", ())
        for index, pattern in enumerate(patterns):
            entries.append((pattern.priority, cls.name, index, cls.name, pattern))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    return tuple((name, pattern) for _p, _n, _i, name, pattern in entries)
