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
from jarvis.tools.registry import EmptyArgs, ToolRegistry, ToolResult


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
