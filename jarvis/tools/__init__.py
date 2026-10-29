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
from jarvis.tools.risk import RiskLevel, risk_level_for_tool, should_confirm
from jarvis.tools.sandbox import PathValidator, SandboxViolationError


def setup_local_tools(
    registry: ToolRegistry,
    config: object = None,
    *,
    ollama_client: object = None,
    vram_manager: object = None,
    memory_store: object = None,
    skill_forge: object = None,
    stt: object = None,
    tts_manager: object = None,
    personal_aliases: object = None,
    game_mode: object = None,
    on_timer_done: Callable[[str], None] | None = None,
    reminder_store: object = None,
    proactive_engine: object = None,
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
    from jarvis.core.config import SandboxConfig

    # Password vault tools
    from jarvis.tools.local.auto_login import AutoLoginTool
    from jarvis.tools.local.bookmarks import BookmarksTool
    from jarvis.tools.local.browser_tabs import BrowserTabsTool
    from jarvis.tools.local.calculator import CalculatorTool
    from jarvis.tools.local.cli_runner import CLIRunnerTool
    from jarvis.tools.local.clipboard import ClipboardTool
    from jarvis.tools.local.close_app import CloseAppTool
    from jarvis.tools.local.code_runner import CodeRunnerTool
    from jarvis.tools.local.content_reader import ContentReaderTool
    from jarvis.tools.local.converter import ConverterTool
    from jarvis.tools.local.dictation import DictationTool
    from jarvis.tools.local.disk_cleaner import (
        CleanTempFilesTool,
        DiskSpaceTool,
        FindLargeFoldersTool,
    )

    # Phase 6 batch 2: Windows tools
    from jarvis.tools.local.display_manager import (
        DisplayInfoTool,
        SetBrightnessTool,
        ToggleNightModeTool,
    )
    from jarvis.tools.local.download_manager import DownloadFileTool
    from jarvis.tools.local.file_manager import (
        ArchiveTool,
        CreateFileTool,
        FileManagerTool,
    )
    from jarvis.tools.local.files import ListDirectoryTool
    from jarvis.tools.local.focus_mode import (
        FocusModeStatusTool,
        StartFocusModeTool,
        StopFocusModeTool,
    )
    from jarvis.tools.local.full_page_screenshot import FullPageScreenshotTool
    from jarvis.tools.local.game_saves import (
        BackupGameSavesTool,
        FindGameSavesTool,
        ListKnownGamesTool,
    )
    from jarvis.tools.local.get_time import GetTimeTool
    from jarvis.tools.local.git_tool import GitTool
    from jarvis.tools.local.hotkey_sender import SendHotkeyTool

    # Image generation
    from jarvis.tools.local.image_gen_tool import ImageGenTool
    from jarvis.tools.local.image_scraper import ScrapeImagesTool
    from jarvis.tools.local.launch_steam_game import LaunchSteamGameTool
    from jarvis.tools.local.launch_workspace import LaunchWorkspaceTool
    from jarvis.tools.local.lock_screen import LockScreenTool
    from jarvis.tools.local.media_control import MediaControlTool
    from jarvis.tools.local.mouse_control import (
        MouseClickTool,
        MouseDragTool,
        MouseMoveTool,
        MousePositionTool,
        MouseScrollTool,
    )
    from jarvis.tools.local.network_tools import (
        MyIpTool,
        NetworkInfoTool,
        SpeedTestTool,
    )
    from jarvis.tools.local.open_app import OpenAppTool
    from jarvis.tools.local.open_url import OpenUrlTool
    from jarvis.tools.local.package_manager import (
        PackageInstallTool,
        PackageSearchTool,
        PackageUninstallTool,
    )
    from jarvis.tools.local.page_compare import ComparePagesTool
    from jarvis.tools.local.page_monitor import (
        CheckPageChangesTool,
        ListPageMonitorsTool,
        StartPageMonitorTool,
        StopPageMonitorTool,
    )
    from jarvis.tools.local.page_translate import TranslatePageTool
    from jarvis.tools.local.password_gen import GeneratePasswordTool
    from jarvis.tools.local.play_youtube_music import PlayYoutubeMusicTool
    from jarvis.tools.local.power_control import PowerControlTool

    # Phase 7 batch 2: browser-power tools
    from jarvis.tools.local.price_lookup import PriceLookupTool
    from jarvis.tools.local.proactive_tools import (
        DismissProactiveTool,
        ProactiveStatusTool,
        SetInitiativeLevelTool,
    )
    from jarvis.tools.local.process_monitor import (
        HeavyProcessesTool,
        ProcessInfoTool,
        WaitForProcessTool,
    )

    # Phase 12: User-defined voice protocols
    from jarvis.tools.local.protocol_tools import (
        CreateProtocolTool,
        DeleteProtocolTool,
        DisableProtocolTool,
        EditProtocolTool,
        EnableProtocolTool,
        ListProtocolsTool,
        RunProtocolTool,
    )
    from jarvis.tools.local.qr_share import GenerateQrTool, ShareToPhoneTool
    from jarvis.tools.local.read_screen_text import ReadScreenTextTool
    from jarvis.tools.local.remind_me import (
        DeleteReminderTool,
        EditReminderTool,
        ListRemindersTool,
        RecurringReminderTool,
        RemindMeAtTool,
        RemindMeTool,
        SnoozeReminderTool,
    )
    from jarvis.tools.local.repeat_last import RepeatLastTool
    from jarvis.tools.local.rss_reader import (
        GetLatestNewsTool,
        ListRssFeedsTool,
        SubscribeRssTool,
        UnsubscribeRssTool,
    )
    from jarvis.tools.local.screenshot import ScreenshotTool
    from jarvis.tools.local.see_screen import SeeScreenTool
    from jarvis.tools.local.skill_tools import (
        AnalyzeSkillsTool,
        ApproveSkillTool,
        DeleteSkillTool,
        DisableAllSkillsTool,
        ImproveSkillTool,
        ListSkillsTool,
        RejectSkillTool,
        SkillDetailTool,
        SkillHistoryTool,
    )
    from jarvis.tools.local.smart_form_fill import SmartFormFillTool
    from jarvis.tools.local.startup_manager import (
        AddToStartupTool,
        ListStartupTool,
        RemoveFromStartupTool,
    )
    from jarvis.tools.local.switch_language import SwitchLanguageTool
    from jarvis.tools.local.system_stats import SystemStatsTool

    # Phase 7 batch 3: browser/cross tools
    from jarvis.tools.local.tab_manager import (
        CloseOtherTabsTool,
        CountTabsTool,
        DuplicateTabTool,
        FindTabTool,
    )
    from jarvis.tools.local.timers import CancelTimerTool, ListTimersTool, SetTimerTool, TimerStore
    from jarvis.tools.local.type_into_active_window import TypeIntoActiveWindowTool
    from jarvis.tools.local.vault_manager import (
        ChangeVaultPasswordTool,
        ListSavedSitesTool,
        LockVaultTool,
        RemoveCredentialTool,
        SaveCredentialTool,
        UnlockVaultTool,
    )
    from jarvis.tools.local.volume import VolumeTool

    # Phase 8: Vision tools
    from jarvis.tools.local.watch_screen import WatchScreenTool
    from jarvis.tools.local.weather import WeatherTool
    from jarvis.tools.local.web_knowledge import SavePageKnowledgeTool
    from jarvis.tools.local.win_notifications import (
        ScheduleNotificationTool,
        ShowNotificationTool,
    )

    # Phase 6: Windows control modules
    from jarvis.tools.local.window_manager import (
        ListWindowsTool,
        MinimizeAllTool,
        RestoreAllTool,
        SnapLayoutTool,
        VirtualDesktopTool,
        WindowActionTool,
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

    from jarvis.core.config import ImageGenConfig
    image_gen_cfg = getattr(config, "image_gen", None) if config is not None else None
    if image_gen_cfg is None:
        image_gen_cfg = ImageGenConfig()

    # Timer store: when on_timer_done is provided, all timer tools share a
    # single TimerStore that fires the callback on expiry.  Otherwise the
    # tools fall back to the module-level lazy singleton (no notification).
    timer_store: TimerStore | None = None
    if on_timer_done is not None:
        timer_store = TimerStore(on_timer_done=on_timer_done)

    base_tools: list[Tool[Any]] = [
        ClipboardTool(),
        CalculatorTool(),
        DictationTool(),
        ConverterTool(),
        BrowserTabsTool(),
        ContentReaderTool(),
        BookmarksTool(),
        MediaControlTool(),
        MouseClickTool(),
        MouseMoveTool(),
        MouseScrollTool(),
        MouseDragTool(),
        MousePositionTool(),
        CloseAppTool(),
        GetTimeTool(),
        FileManagerTool(path_validator=path_validator),
        CreateFileTool(path_validator=path_validator),
        ArchiveTool(path_validator=path_validator),
        ListDirectoryTool(path_validator=path_validator),
        GitTool(),
        LockScreenTool(),
        OpenAppTool(personal_aliases=personal_aliases),
        OpenUrlTool(),
        LaunchSteamGameTool(),
        LaunchWorkspaceTool(workspace_apps=workspace_apps),
        PlayYoutubeMusicTool(),
        ScreenshotTool(),
        SystemStatsTool(vram_manager=vram_manager),
        ReadScreenTextTool(),
        RepeatLastTool(),
        TypeIntoActiveWindowTool(),
        SwitchLanguageTool(stt=stt, tts_manager=tts_manager),
        VolumeTool(),
        WeatherTool(weather_config=weather_cfg, save_fn=weather_save_fn),
        # Phase 6: Windows control tools
        WindowActionTool(),
        ListWindowsTool(),
        SnapLayoutTool(),
        MinimizeAllTool(),
        RestoreAllTool(),
        VirtualDesktopTool(),
        SetTimerTool(store=timer_store),
        CancelTimerTool(store=timer_store),
        ListTimersTool(store=timer_store),
        PowerControlTool(),
        CLIRunnerTool(),
        CodeRunnerTool(path_validator=path_validator),
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
        RemindMeTool(store=reminder_store),
        RemindMeAtTool(store=reminder_store),
        ListRemindersTool(store=reminder_store),
        DeleteReminderTool(store=reminder_store),
        RecurringReminderTool(store=reminder_store),
        SnoozeReminderTool(store=reminder_store),
        EditReminderTool(store=reminder_store),
        SetInitiativeLevelTool(engine=proactive_engine),
        ProactiveStatusTool(engine=proactive_engine),
        DismissProactiveTool(engine=proactive_engine),
        # SkillForge tools
        ListSkillsTool(forge=skill_forge),
        SkillDetailTool(forge=skill_forge),
        ApproveSkillTool(forge=skill_forge),
        RejectSkillTool(forge=skill_forge),
        DeleteSkillTool(forge=skill_forge),
        DisableAllSkillsTool(forge=skill_forge),
        AnalyzeSkillsTool(forge=skill_forge),
        ImproveSkillTool(forge=skill_forge),
        SkillHistoryTool(forge=skill_forge),
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
        # Image generation
        ImageGenTool(
            image_gen_config=image_gen_cfg,
            ollama_client=ollama_client,
        ),
        # Phase 12: Protocol tools (engine wired below after registration)
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

    # Phase 12: Protocol tools — engine is created here so it gets
    # the registry reference for step execution and LLM for complex parsing.
    from jarvis.skills.protocols import ProtocolEngine
    protocol_engine = ProtocolEngine(
        registry=registry,
        ollama_client=ollama_client,
    )
    protocol_tools_list = [
        CreateProtocolTool(engine=protocol_engine),
        RunProtocolTool(engine=protocol_engine),
        ListProtocolsTool(engine=protocol_engine),
        DeleteProtocolTool(engine=protocol_engine),
        EditProtocolTool(engine=protocol_engine),
        EnableProtocolTool(engine=protocol_engine),
        DisableProtocolTool(engine=protocol_engine),
    ]
    for tool in protocol_tools_list:
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
        from jarvis.tools.local.youtube_summary import YoutubeSummaryTool as _YSTool
        registry.register(_YSTool())


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
