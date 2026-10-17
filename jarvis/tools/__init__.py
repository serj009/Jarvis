"""Tool protocol, registry, and tool implementations.

Public surface:
  - Tool, ToolResult, EmptyArgs:            protocol + return type
  - ConfirmationRequest, ToolConfirmer:    the requires_confirmation gate
  - VoicePattern, VoiceRoutable:           optional voice-routing extension
  - ToolRegistry, ToolNameCollisionError:  registry + collision exception
  - TOOL_NAME_REGEX:                       shared name-validity regex

Local tool implementations live under jarvis/tools/local/. MCP-adapted
tools live under jarvis/tools/mcp_client.py (Phase 4 Task 3). The static
catalogue of local tool classes — which drives the router's voice-pattern
table and the Settings -> Tools list — is jarvis/tools/catalogue.py.
"""

from collections.abc import Callable
from typing import Any, cast

from jarvis.tools.risk import RiskLevel, risk_level_for_tool, should_confirm
from jarvis.tools.sandbox import PathValidator, SandboxViolationError
from jarvis.tools.mcp_client import MCPManager, MCPServerConnection, MCPTool
from jarvis.tools.registry import (
    PRIORITY_CATCH_ALL,
    PRIORITY_DEFAULT,
    TOOL_NAME_REGEX,
    ConfirmationRequest,
    EmptyArgs,
    Tool,
    ToolConfirmer,
    ToolNameCollisionError,
    ToolRegistry,
    ToolResult,
    VoicePattern,
    VoiceRoutable,
)


def setup_local_tools(
    registry: ToolRegistry,
    config: object = None,
    *,
    ollama_client: object = None,
    vram_manager: object = None,
    memory_store: object = None,
    stt: object = None,
    tts_manager: object = None,
    personal_aliases: object = None,
    game_mode: object = None,
) -> None:
    """Register every built-in local tool into `registry`.

    Built-ins register before any MCP-adapted tools (see registry.py
    header on the name-collision policy): an MCP server publishing a
    tool whose name clashes with a built-in is refused, and the
    built-in stays. Add new local tools here in the same lexical order
    as their filenames so reviewers can audit the set against
    jarvis/tools/local/ at a glance.

    `config` is the live JarvisConfig instance. Tools that need config
    (e.g. WeatherTool) receive their sub-config section at construction.
    `ollama_client` is the live OllamaClient — required for tools that
    invoke vision models (SeeScreenTool); passing None disables those tools
    cleanly (they fail-fast with a "no vision available" error).
    `vram_manager` is the live VRAMManager (T1.4) — passed to SystemStatsTool
    so it can report GPU/VRAM status; None disables GPU reporting."""
    from jarvis.tools.local.clipboard import ClipboardTool
    from jarvis.core.config import SandboxConfig
    from jarvis.tools.local.close_app import CloseAppTool
    from jarvis.tools.local.calculator import CalculatorTool
    from jarvis.tools.local.converter import ConverterTool
    from jarvis.tools.local.file_manager import FileManagerTool
    from jarvis.tools.local.files import ListDirectoryTool
    from jarvis.tools.local.get_time import GetTimeTool
    from jarvis.tools.local.web_knowledge import SavePageKnowledgeTool
    from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool
    from jarvis.tools.local.launch_workspace import LaunchWorkspaceTool
    from jarvis.tools.local.lock_screen import LockScreenTool
    from jarvis.tools.local.open_app import OpenAppTool
    from jarvis.tools.local.open_url import OpenUrlTool
    from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
    from jarvis.tools.local.screenshot import ScreenshotTool
    from jarvis.tools.local.browser_tabs import BrowserTabsTool
    from jarvis.tools.local.content_reader import ContentReaderTool
    from jarvis.tools.local.bookmarks import BookmarksTool
    from jarvis.tools.local.media_control import MediaControlTool
    from jarvis.tools.local.switch_language import SwitchLanguageTool
    from jarvis.tools.local.see_screen import SeeScreenTool
    from jarvis.tools.local.system_stats import SystemStatsTool
    from jarvis.tools.local.type_into_active_window import TypeIntoActiveWindowTool
    from jarvis.tools.local.volume import VolumeTool
    from jarvis.tools.local.weather import WeatherTool

    # Phase 6: Windows control modules
    from jarvis.tools.local.window_manager import WindowActionTool, ListWindowsTool
    from jarvis.tools.local.timers import SetTimerTool, CancelTimerTool, ListTimersTool
    from jarvis.tools.local.power_control import PowerControlTool
    from jarvis.tools.local.cli_runner import CLIRunnerTool
    from jarvis.tools.local.download_manager import DownloadFileTool
    from jarvis.tools.local.package_manager import (
        PackageSearchTool,
        PackageInstallTool,
        PackageUninstallTool,
    )
    # Phase 7 batch 2: browser-power tools
    from jarvis.tools.local.price_lookup import PriceLookupTool
    from jarvis.tools.local.full_page_screenshot import FullPageScreenshotTool
    from jarvis.tools.local.image_scraper import ScrapeImagesTool
    from jarvis.tools.local.game_saves import (
        FindGameSavesTool, BackupGameSavesTool, ListKnownGamesTool,
    )
    from jarvis.tools.local.process_monitor import (
        ProcessInfoTool, HeavyProcessesTool, WaitForProcessTool,
    )
    from jarvis.tools.local.page_monitor import (
        StartPageMonitorTool, StopPageMonitorTool,
        CheckPageChangesTool, ListPageMonitorsTool,
    )
    from jarvis.tools.local.page_compare import ComparePagesTool
    from jarvis.tools.local.smart_form_fill import SmartFormFillTool
    from jarvis.tools.local.page_translate import TranslatePageTool
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
    from jarvis.tools.local.remind_me import (
        RemindMeTool,
        RemindMeAtTool,
        ListRemindersTool,
        DeleteReminderTool,
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

    weather_cfg = getattr(config, "weather", None)
    if weather_cfg is None:
        from jarvis.core.config import WeatherConfig
        weather_cfg = WeatherConfig()

    # Declared separately from the closure below: binding the name to
    # None first and then to a `def` of the same name gives the name two
    # incompatible declared types. The closure keeps its own reference to
    # the config so the narrowing survives into the call.
    weather_save_fn: Callable[[], None] | None = None
    if config is not None:
        from jarvis.core.config import JarvisConfig
        from jarvis.core.config import save_config as _save_cfg

        saved_config = config

        def weather_save_fn_impl() -> None:
            _save_cfg(cast(JarvisConfig, saved_config))

        weather_save_fn = weather_save_fn_impl

    from jarvis.core.config import WorkspaceConfig

    ws_cfg = getattr(config, "workspace", None) if config is not None else None
    workspace_apps = list(
        (ws_cfg or WorkspaceConfig()).apps
    )

    # T5.1: Build the path validator from the sandbox config.
    sandbox_cfg = getattr(config, "sandbox", None) if config is not None else None
    if sandbox_cfg is None:
        sandbox_cfg = SandboxConfig()
    from pathlib import Path as _Path
    sandbox_paths = [_Path(p).expanduser() for p in sandbox_cfg.allowed_paths]
    path_validator = PathValidator(
        allowed_paths=sandbox_paths,
        allow_home_fallback=True,
    )

    vision_cfg = getattr(config, "vision", None) if config is not None else None
    if vision_cfg is None:
        from jarvis.core.config import VisionConfig
        vision_cfg = VisionConfig()

    base_tools: list[Tool[Any]] = [
        ClipboardTool(),
        CalculatorTool(),
        ConverterTool(),
        BrowserTabsTool(),
        ContentReaderTool(),
        BookmarksTool(),
        MediaControlTool(),
        CloseAppTool(),
        GetTimeTool(),
        FileManagerTool(path_validator=path_validator),
        ListDirectoryTool(path_validator=path_validator),
        LockScreenTool(),
        OpenAppTool(personal_aliases=personal_aliases),
        OpenUrlTool(),
        LaunchSteamGameTool(),
        LaunchWorkspaceTool(workspace_apps=workspace_apps),
        PlayYoutubeMusicTool(),
        ScreenshotTool(),
        SystemStatsTool(vram_manager=vram_manager),
        TypeIntoActiveWindowTool(),
        SwitchLanguageTool(stt=stt, tts_manager=tts_manager),
        VolumeTool(),
        WeatherTool(weather_config=weather_cfg, save_fn=weather_save_fn),
        # Phase 6: Windows control tools
        WindowActionTool(),
        ListWindowsTool(),
        SetTimerTool(),
        CancelTimerTool(),
        ListTimersTool(),
        PowerControlTool(),
        CLIRunnerTool(),
        DownloadFileTool(),
        PackageSearchTool(),
        PackageInstallTool(),
        PackageUninstallTool(),
        FindGameSavesTool(),
        BackupGameSavesTool(),
        ListKnownGamesTool(),
        ProcessInfoTool(),
        HeavyProcessesTool(),
        WaitForProcessTool(),
        # Phase 7 batch 2: browser-power tools
        PriceLookupTool(),
        FullPageScreenshotTool(),
        ScrapeImagesTool(),
        # Page monitoring / comparison / form fill
        StartPageMonitorTool(),
        StopPageMonitorTool(),
        CheckPageChangesTool(),
        ListPageMonitorsTool(),
        SmartFormFillTool(),
        # Phase 6 batch 2: Windows tools
        SetBrightnessTool(),
        ToggleNightModeTool(),
        DisplayInfoTool(),
        ListStartupTool(),
        RemoveFromStartupTool(),
        AddToStartupTool(),
        MyIpTool(),
        SpeedTestTool(),
        NetworkInfoTool(),
        DiskSpaceTool(),
        FindLargeFoldersTool(),
        CleanTempFilesTool(),
        SendHotkeyTool(),
        ShowNotificationTool(),
        ScheduleNotificationTool(),
        # Phase 7 batch 3: browser/cross tools
        CloseOtherTabsTool(),
        CountTabsTool(),
        FindTabTool(),
        DuplicateTabTool(),
        GeneratePasswordTool(),
        SubscribeRssTool(),
        UnsubscribeRssTool(),
        ListRssFeedsTool(),
        GetLatestNewsTool(),
        GenerateQrTool(),
        ShareToPhoneTool(),
        StartFocusModeTool(),
        # Time-based persistent reminders
        RemindMeTool(),
        RemindMeAtTool(),
        ListRemindersTool(),
        DeleteReminderTool(),
        StopFocusModeTool(),
        FocusModeStatusTool(),
        # Phase 8: Vision tools
        WatchScreenTool(),
        # Password vault tools
        AutoLoginTool(),
        SaveCredentialTool(),
        ListSavedSitesTool(),
        RemoveCredentialTool(),
        UnlockVaultTool(),
        LockVaultTool(),
        ChangeVaultPasswordTool(),
    ]
    # SeeScreenTool needs an OllamaClient to call /api/chat with images.
    # Tests that pass None happily register every text-only tool but skip
    # vision — keeps the test harness from having to mock httpx everywhere.
    if ollama_client is not None:
        base_tools.append(
            SeeScreenTool(
                ollama_client=ollama_client,
                vision_config=vision_cfg,
                game_mode=game_mode,
            )
        )

    for tool in base_tools:
        registry.register(tool)

    # Web knowledge extraction — needs ollama_client + memory_store + relation_store.
    # relation_store lives on memory_store._db_path via MemoryManager; we get it
    # from the memory_store's db_path so the tool can create one if needed.
    if ollama_client is not None and memory_store is not None:
        relation_store = None
        try:
            from jarvis.memory.relations import RelationStore
            relation_store = RelationStore(db_path=memory_store.db_path)
        except Exception:
            pass
        registry.register(SavePageKnowledgeTool(
            ollama_client=ollama_client,
            memory_store=memory_store,
            relation_store=relation_store,
        ))

    # Page comparison & translation — need ollama_client.
    if ollama_client is not None:
        registry.register(ComparePagesTool(
            ollama_client=ollama_client,
        ))
        registry.register(TranslatePageTool(
            ollama_client=ollama_client,
        ))

    # T4.1: Memory tools — remember, recall, forget, status.
    if memory_store is not None:
        from jarvis.memory.tools import (
            ForgetTool,
            MemoryStatusTool,
            RecallTool,
            RememberTool,
        )
        for tool in [RememberTool, RecallTool, ForgetTool, MemoryStatusTool]:
            registry.register(tool(memory_store=memory_store))

    # YouTube summary — needs ollama_client for LLM summarisation.
    if ollama_client is not None:
        from jarvis.tools.local.youtube_summary import YoutubeSummaryTool as _YSTool
        registry.register(_YSTool(ollama_client=ollama_client))
    else:
        # Register without LLM — tool will fail-fast with a clear error.
        pass


__all__ = [
    "PRIORITY_CATCH_ALL",
    "PRIORITY_DEFAULT",
    "TOOL_NAME_REGEX",
    "ConfirmationRequest",
    "EmptyArgs",
    "MCPManager",
    "MCPServerConnection",
    "MCPTool",
    "Tool",
    "ToolConfirmer",
    "ToolNameCollisionError",
    "ToolRegistry",
    "ToolResult",
    "VoicePattern",
    "VoiceRoutable",
    "setup_local_tools",
    "RiskLevel",
    "risk_level_for_tool",
    "should_confirm",
    "PathValidator",
    "SandboxViolationError",
]
