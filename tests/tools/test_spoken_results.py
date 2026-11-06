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
