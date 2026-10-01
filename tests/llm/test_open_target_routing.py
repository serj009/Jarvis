"""IntentRouter fast path for known open targets (open_target_planner).

The planner is injected, so these tests never touch this PC's
installed-apps index."""

from __future__ import annotations

from typing import Any

from jarvis.core.config import ToolsConfig
from jarvis.llm.conversation import Conversation
from jarvis.llm.intent_router import IntentRouter, SpeakIntent, ToolIntent
from jarvis.llm.ollama_client import ChatChunk
from jarvis.tools.local.open_targets import make_installed_app_planner
from jarvis.tools.registry import EmptyArgs, ToolRegistry, ToolResult


class CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, messages, *, tools=None):
        self.calls += 1
        yield ChatChunk(content="llm answer", done=True)


class _Tool:
    args_schema = EmptyArgs
    requires_confirmation = False

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Fake {name}."

    async def execute(self, args) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _router(planner: Any, *tool_names: str) -> tuple[IntentRouter, CountingLLM]:
    registry = ToolRegistry(ToolsConfig())
    for name in tool_names:
        registry.register(_Tool(name))
    llm = CountingLLM()
    router = IntentRouter(
        llm=llm,  # type: ignore[arg-type]
        conversation=Conversation(system_prompt_provider=lambda: "sys"),
        registry=registry,
        open_target_planner=planner,
    )
    return router, llm


async def _collect(router: IntentRouter, text: str) -> list:
    return [i async for i in router.route(text)]


async def test_known_site_bypasses_the_llm():
    planner = make_installed_app_planner(lambda: {})
    router, llm = _router(planner, "open_url", "open_app")

    intents = await _collect(router, "Открой YouTube.")

    assert intents == [ToolIntent("open_url", {"url": "https://www.youtube.com"})]
    assert llm.calls == 0


async def test_installed_app_wins():
    planner = make_installed_app_planner(lambda: {"steam": object()})
    router, llm = _router(planner, "open_url", "open_app")

    intents = await _collect(router, "Відкрий стім")

    assert intents == [ToolIntent("open_app", {"name": "steam"})]
    assert llm.calls == 0


async def test_unknown_name_goes_to_the_llm():
    calls: list[str] = []

    def provider():
        calls.append("scan")
        return {}

    router, llm = _router(make_installed_app_planner(provider), "open_url", "open_app")

    intents = await _collect(router, "Открой блокнот")

    assert llm.calls == 1
    assert isinstance(intents[0], SpeakIntent)
    assert calls == []  # index not even consulted for unknown names


async def test_planner_failure_falls_back_to_the_llm():
    async def broken(text):
        raise RuntimeError("boom")

    router, llm = _router(broken, "open_url", "open_app")
    await _collect(router, "Открой YouTube")
    assert llm.calls == 1


async def test_unregistered_tool_falls_back_to_the_llm():
    planner = make_installed_app_planner(lambda: {})
    router, llm = _router(planner, "open_app")  # open_url disabled/missing
    await _collect(router, "Открой YouTube")
    assert llm.calls == 1


async def test_index_failure_still_opens_the_website():
    def provider():
        raise OSError("registry unavailable")

    router, llm = _router(make_installed_app_planner(provider), "open_url", "open_app")

    intents = await _collect(router, "Открой стим")

    assert intents == [ToolIntent("open_url", {"url": "https://store.steampowered.com"})]
    assert llm.calls == 0


async def test_no_planner_keeps_old_behaviour():
    router, llm = _router(None, "open_url", "open_app")
    await _collect(router, "Открой YouTube")
    assert llm.calls == 1
