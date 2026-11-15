"""ToolResult.spoken: voice-routed tools answer in the request's language.

Before, a tool run by a voice pattern had its `output` read out verbatim:
English ("Volume up, sir.", "Timer 'timer_1' set for 5m 0s, sir.") with
ids ("(id: 3f2a...)") in a Russian conversation. `spoken` carries the
short sentence for the voice; `output` stays for the model.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from jarvis.core.config import ToolsConfig
from jarvis.core.request_context import current_user_transcription
from jarvis.llm.intent_router import ToolIntent, execute_intent
from jarvis.memory.scheduled_reminders import ReminderStore
from jarvis.tools.local.remind_me import RemindMeTool
from jarvis.tools.local.timers import SetTimerTool, TimerStore
from jarvis.tools.local.volume import VolumeTool
from jarvis.tools.registry import ConfirmationRequest, EmptyArgs, ToolRegistry, ToolResult


async def _say(registry: ToolRegistry, tool: str, args: dict, request: str) -> str:
    token = current_user_transcription.set(request)
    try:
        return "".join([c async for c in execute_intent(ToolIntent(tool, args), registry)])
    finally:
        current_user_transcription.reset(token)


@pytest.fixture
async def registry(tmp_path):
    reg = ToolRegistry(ToolsConfig())
    reg.register(VolumeTool())
    timers = TimerStore(persist_path=tmp_path / "timers.json")
    reg.register(SetTimerTool(store=timers))
    store = ReminderStore(db_path=tmp_path / "reminders.db")
    store.open()
    reg.register(RemindMeTool(store=store))
    yield reg
    for timer in timers.list_timers():
        timers.cancel_timer(timer["name"])
    await asyncio.sleep(0)  # let the cancelled countdowns finish
    store.close()


async def test_volume_answers_in_russian(registry):
    with patch("jarvis.tools.local.volume.winplat.volume_up"):
        spoken = await _say(registry, "volume", {"action": "up"}, "громче")
    assert spoken.strip() == "Громче, сэр."


async def test_timer_answers_in_ukrainian_with_a_spoken_duration(registry):
    request = "постав таймер на п'ять хвилин для піци"
    spoken = await _say(registry, "set_timer", {"seconds": 300}, request)
    assert spoken.strip() == "Таймер на 5 хвилин, сер."


async def test_reminder_is_spoken_without_its_id(registry):
    args = {"seconds": 1800, "text": "купить молоко"}
    spoken = await _say(registry, "remind_me", args, "напомни через 30 минут купить молоко")
    assert spoken.strip() == "Напомню через 30 минут, сэр: купить молоко."
    assert "id" not in spoken


async def test_the_model_still_reads_output_with_the_id(registry):
    result = await registry.execute("remind_me", {"seconds": 60, "text": "чай"})
    assert result.success and isinstance(result.output, str)
    assert "(id: " in result.output


async def test_english_requests_hear_english(registry):
    spoken = await _say(registry, "set_timer", {"seconds": 90}, "set a timer for 90 seconds")
    assert spoken.strip() == "Timer set for 1 minute 30 seconds, sir."


async def test_without_spoken_the_output_is_read_as_before():
    class _Plain:
        name = "plain"
        description = "d"
        requires_confirmation = False
        args_schema = EmptyArgs

        async def execute(self, args: EmptyArgs) -> ToolResult:
            return ToolResult(success=True, output="Done the old way, sir.")

    reg = ToolRegistry(ToolsConfig())
    reg.register(_Plain())  # type: ignore[arg-type]
    assert (await _say(reg, "plain", {}, "сделай")).strip() == "Done the old way, sir."


# -- notes, media keys, windows ------------------------------------------------


class _Approve:
    async def confirm(self, request: ConfirmationRequest) -> bool:
        return True


def _one_tool(tool: object) -> ToolRegistry:
    reg = ToolRegistry(ToolsConfig(), confirmer=_Approve())
    reg.register(tool)  # type: ignore[arg-type]
    return reg


async def test_note_is_confirmed_in_russian():
    from jarvis.tools.local.notes_tools import TakeNoteTool

    reg = _one_tool(TakeNoteTool(on_create=lambda title, content: title))
    spoken = await _say(reg, "take_note", {"content": "купить хлеб"}, "запиши купить хлеб")
    assert spoken.strip() == "Записал, сэр: «купить хлеб»."


async def test_media_key_answers_in_ukrainian():
    from jarvis.tools.local import media_control

    with (
        patch("jarvis.tools.local.browser_tabs._get_browser", side_effect=RuntimeError),
        patch.object(media_control, "_press_media_key", return_value={"success": True}),
    ):
        spoken = await _say(_one_tool(media_control.MediaControlTool()), "media_control",
                            {"action": "next_track"}, "наступний трек")
    assert spoken.strip() == "Наступний трек, сер."


async def test_minimize_all_answers_in_russian():
    from jarvis.tools.local import window_manager

    with patch.object(window_manager, "_send_keys", return_value=True):
        spoken = await _say(_one_tool(window_manager.MinimizeAllTool()), "minimize_all", {},
                            "сверни все окна")
    assert spoken.strip() == "Свернул все окна, сэр."


# -- system tools: numbers are said with their nouns ----------------------------


@pytest.mark.parametrize(
    ("n", "unit", "lang", "said"),
    [
        (50, "percent", "ru", "50 процентов"),
        (21, "percent", "uk", "21 відсоток"),
        (1, "gigabyte", "en", "1 gigabyte"),
        (119.6, "gigabyte", "ru", "120 гигабайт"),
        (3, "file", "uk", "3 файли"),
        (2, "character", "en", "2 characters"),
    ],
)
def test_quantity(n, unit, lang, said):
    from jarvis.core.phrases import quantity

    assert quantity(n, unit, lang) == said


async def test_disk_space_is_said_per_drive():
    from jarvis.tools.local import disk_cleaner

    disks = [{"device": "C:\\", "mountpoint": "C:\\", "fstype": "NTFS", "percent_used": 76,
              "used_gb": 380.2, "total_gb": 500.0, "free_gb": 119.8}]
    with (
        patch.object(disk_cleaner, "_disk_space_impl", return_value=disks),
        patch.object(disk_cleaner, "_get_temp_info", side_effect=OSError),
    ):
        spoken = await _say(_one_tool(disk_cleaner.DiskSpaceTool()), "disk_space", {},
                            "сколько места на диске")
    assert spoken.strip() == "Свободно, сэр: на диске C 120 гигабайт из 500 гигабайт."


async def test_my_ip_in_ukrainian():
    from jarvis.tools.local import network_tools

    info = {"local_ip": "192.168.1.5", "public_ip": None}
    with patch.object(network_tools, "_my_ip_impl", return_value=info):
        spoken = await _say(_one_tool(network_tools.MyIpTool()), "my_ip", {}, "який мій айпі")
    assert spoken.strip().startswith("Локальна IP 192.168.1.5, сер.")


async def test_keeping_the_computer_awake_says_for_how_long():
    from jarvis.tools.local import power_control

    with patch.object(power_control, "_caffeinate", return_value="Preventing sleep."):
        spoken = await _say(
            _one_tool(power_control.PowerControlTool()), "power_control",
            {"action": "caffeinate", "caffeinate_minutes": 90}, "не давай компьютеру уснуть",
        )
    assert spoken.strip() == "Не дам компьютеру уснуть ещё 1 час 30 минут, сэр."


# -- panels, dictation, notifications ------------------------------------------


async def test_clearing_clipboard_history_counts_items_in_russian():
    from jarvis.tools.local.clipboard_history_tools import ClearClipboardHistoryTool

    reg = _one_tool(ClearClipboardHistoryTool(on_clear=lambda: 3))
    spoken = await _say(reg, "clear_clipboard_history", {}, "очисти историю буфера")
    assert spoken.strip() == "Удалил из истории буфера: 3 записи, сэр."


async def test_dictation_tells_how_to_stop_in_ukrainian():
    from jarvis.tools.local import dictation

    reg = _one_tool(dictation.DictationTool())
    try:
        spoken = await _say(reg, "dictation", {"action": "start"}, "почни диктовку")
    finally:
        dictation._set_dictation(False)
    assert "«заверши диктовку»" in spoken
    assert spoken.startswith("Диктування ввімкнено, сер.")


async def test_logs_panel_answers_in_russian():
    from jarvis.tools.local.log_tools import ShowLogsTool

    reg = _one_tool(ShowLogsTool(on_open=lambda: None))
    assert (await _say(reg, "show_logs", {}, "покажи логи")).strip() == "Показываю логи, сэр."


def _files(tmp_path):
    from jarvis.tools.local.file_manager import FileManagerTool
    from jarvis.tools.sandbox import PathValidator

    validator = PathValidator(allowed_paths=[tmp_path], allow_home_fallback=False)
    return _one_tool(FileManagerTool(path_validator=validator))


async def test_file_search_is_counted_in_russian(tmp_path):
    for name in ("отчет-1.txt", "отчет-2.txt", "отчет-3.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    spoken = await _say(_files(tmp_path), "file_manager",
                        {"action": "find", "path": "отчет", "search_dir": str(tmp_path)},
                        "найди файл отчет")
    assert spoken.startswith("Нашёл 3 файла, сэр: ")
    assert "sir" not in spoken


async def test_deleting_a_file_says_how_to_undo_in_ukrainian(tmp_path):
    (tmp_path / "test.txt").write_text("x", encoding="utf-8")
    spoken = await _say(_files(tmp_path), "file_manager",
                        {"action": "delete", "path": str(tmp_path / "test.txt")},
                        "видали файл test.txt зі столу")
    assert spoken.strip() == "Видалив test.txt, сер. Скажіть «поверни як було», щоб повернути."


async def test_creating_a_folder_answers_in_russian(tmp_path):
    spoken = await _say(_files(tmp_path), "file_manager",
                        {"action": "mkdir", "path": str(tmp_path / "проекты")},
                        "создай папку проекты")
    assert spoken.strip() == "Создал папку проекты, сэр."


async def test_bookmarks_list_names_only_in_russian(tmp_path):
    from jarvis.tools.local import bookmarks

    saved = [{"name": "Почта", "url": "https://mail.example"},
             {"name": "Новости", "url": "https://news.example"}]
    with patch.object(bookmarks, "_load_bookmarks", return_value=saved):
        spoken = await _say(_one_tool(bookmarks.BookmarksTool()), "bookmarks",
                            {"action": "list"}, "покажи мои закладки")
    assert spoken.strip() == "Закладок: 2, сэр: Почта, Новости."


def test_tab_count_in_ukrainian_and_english():
    from jarvis.tools.local.browser_tabs import BrowserTabsTool

    token = current_user_transcription.set("скільки вкладок відкрито")
    try:
        assert BrowserTabsTool._said("list_tabs", {"count": 3}) == "Вкладок відкрито: 3, сер."
    finally:
        current_user_transcription.reset(token)
    token = current_user_transcription.set("how many tabs are open")
    try:
        assert BrowserTabsTool._said("list_tabs", {"count": 1}) == "1 tab open, sir."
        assert BrowserTabsTool._said("close_tab", {"remaining_tabs": 3}) == (
            "Tab closed, sir. 3 tabs left."
        )
    finally:
        current_user_transcription.reset(token)


async def test_double_click_without_coordinates_in_russian():
    from unittest.mock import MagicMock

    from jarvis.tools.local import mouse_control

    gui = MagicMock()
    gui.size.return_value = (1920, 1080)
    with patch.object(mouse_control, "_get_pyautogui", return_value=gui):
        spoken = await _say(_one_tool(mouse_control.MouseClickTool()), "mouse_click",
                            {"x": 100, "y": 200, "button": "double"}, "двойной клик по 100 200")
    assert spoken.strip() == "Дважды кликнул, сэр."


async def test_scroll_up_in_ukrainian():
    from unittest.mock import MagicMock

    from jarvis.tools.local import mouse_control

    with patch.object(mouse_control, "_get_pyautogui", return_value=MagicMock()):
        spoken = await _say(_one_tool(mouse_control.MouseScrollTool()), "mouse_scroll",
                            {"direction": "up", "amount": 3}, "прокрути вгору сторінку")
    assert spoken.strip() == "Прокрутив угору, сер."


async def test_process_usage_in_russian():
    from jarvis.tools.local import process_monitor

    info = {"success": True, "process_count": 2, "total_memory_mb": 512, "total_cpu_percent": 3.4}
    with patch.object(process_monitor, "_get_process_info", return_value=info):
        spoken = await _say(_one_tool(process_monitor.ProcessInfoTool()), "process_info",
                            {"name": "chrome"}, "сколько памяти ест chrome")
    assert spoken.strip() == "chrome: 512 мегабайт памяти, процессор 3%, сэр."


async def test_removing_a_missing_startup_entry_does_not_claim_success():
    from jarvis.tools.local import startup_manager

    with patch.object(startup_manager, "_remove_from_startup_impl",
                      return_value="'spotify' not found in startup entries, sir."):
        spoken = await _say(_one_tool(startup_manager.RemoveFromStartupTool()),
                            "remove_from_startup", {"name": "spotify"},
                            "убери spotify из автозагрузки")
    assert spoken.strip() == "spotify нет в автозагрузке, сэр."


async def test_startup_list_names_programs_in_russian():
    from jarvis.tools.local import startup_manager

    entries = [{"name": "Steam", "source": "HKCU Run", "command": "steam.exe", "removable": True},
               {"name": "Ollama", "source": "Startup", "command": "ollama.exe"}]
    with patch.object(startup_manager, "_list_startup_impl", return_value=entries):
        spoken = await _say(_one_tool(startup_manager.ListStartupTool()), "list_startup", {},
                            "покажи автозагрузку")
    assert spoken.strip() == "В автозагрузке программ: 2, сэр: Steam, Ollama."


async def test_package_install_in_ukrainian():
    from jarvis.tools.local import package_manager

    with patch.object(package_manager, "_run_winget", return_value={"success": True}):
        spoken = await _say(_one_tool(package_manager.PackageInstallTool()), "package_install",
                            {"package": "Spotify"}, "встанови spotify, будь ласка, зі стору")
    assert spoken.strip() == "Встановив Spotify, сер."


async def test_package_search_does_not_read_the_table_aloud():
    from jarvis.tools.local import package_manager

    table = "Name  Id  Version\nSpotify  Spotify.Spotify  1.2"
    with patch.object(package_manager, "_run_winget",
                      return_value={"success": True, "output": table}):
        spoken = await _say(_one_tool(package_manager.PackageSearchTool()), "package_search",
                            {"query": "spotify"}, "найди пакет spotify")
    assert spoken.strip() == "Вот что нашлось по запросу «spotify», сэр."


async def test_game_saves_found_in_russian(tmp_path):
    from jarvis.tools.local import game_saves

    with patch.object(game_saves, "_find_save_path", return_value=tmp_path):
        spoken = await _say(_one_tool(game_saves.FindGameSavesTool()), "find_game_saves",
                            {"game": "dark souls 2"}, "где сохранения дарк соулс 2")
    assert spoken.strip() == "Нашёл сохранения Dark Souls II, сэр."


async def test_vault_list_says_site_names_and_never_a_password():
    from unittest.mock import MagicMock

    from jarvis.tools.local.vault_manager import ListSavedSitesTool

    vault = MagicMock()
    vault.is_unlocked = True
    vault.list_sites.return_value = ["steam", "github"]
    spoken = await _say(_one_tool(ListSavedSitesTool(vault=vault)), "list_saved_sites", {},
                        "какие пароли сохранены")
    assert spoken.strip() == "Пароли сохранены для: github, steam, сэр."
    vault.get_credential.assert_not_called()


async def test_news_reads_three_headlines_without_links():
    from jarvis.tools.local import rss_reader

    feeds = [{"name": "Новости", "url": "https://news.example/rss"}]
    items = [{"title": f"Заголовок {i}", "link": f"https://news.example/{i}"} for i in range(5)]
    with (
        patch.object(rss_reader, "_load_feeds", return_value=feeds),
        patch.object(rss_reader, "_fetch_feed", return_value=("<rss/>", None)),
        patch.object(rss_reader, "_parse_feed_xml", return_value=items),
    ):
        spoken = await _say(_one_tool(rss_reader.GetLatestNewsTool()), "get_latest_news",
                            {"count": 5}, "какие новости")
    assert spoken.strip() == "Вот новости, сэр: Заголовок 0; Заголовок 1; Заголовок 2."
    assert "https://" not in spoken
