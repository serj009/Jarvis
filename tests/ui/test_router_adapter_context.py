"""The router adapter's per-turn context variable survives barge-in.

When the user says the wake word while Jarvis is speaking, the pipeline
abandons the response generator, and it is closed from a different task
than the one that started it. Resetting the ContextVar token there raised
"ValueError: <Token ...> was created in a different Context" -- logged as
an unhandled asyncio exception after every interrupted answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest

from jarvis.app import _make_router_adapter
from jarvis.core.request_context import current_user_transcription
from jarvis.llm.intent_router import SpeakIntent


class _Router:
    async def route(self, transcription: str):
        for word in ("one", "two", "three"):
            yield SpeakIntent(text=word)


def _producer():
    return _make_router_adapter(_Router(), MagicMock(), MagicMock())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_closing_from_another_task_does_not_raise():
    gen = _producer()("что осталось")
    assert isinstance(gen, AsyncGenerator)
    assert await gen.__anext__() == "one"
    # Barge-in: the generator is closed by a different task / context.
    await asyncio.create_task(gen.aclose())


@pytest.mark.asyncio
async def test_transcription_is_visible_during_the_turn_and_reset_after():
    seen: list[str | None] = []

    class _Peeking:
        async def route(self, transcription: str):
            seen.append(current_user_transcription.get())
            yield SpeakIntent(text="ok")

    producer = _make_router_adapter(_Peeking(), MagicMock(), MagicMock())  # type: ignore[arg-type]
    assert [chunk async for chunk in producer("запомни эту страницу")] == ["ok"]
    assert seen == ["запомни эту страницу"]
    assert current_user_transcription.get() is None
