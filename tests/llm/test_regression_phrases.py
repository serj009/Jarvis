"""Regression run of the control phrases (M3-2).

Every phrase in tests/fixtures/regression_phrases.yaml goes through the
whole router, as a transcription would: normalisation, the pattern table
and the open-target planner. A phrase with a `tool` must be decided by a
pattern, with exactly those arguments and without the model; a phrase
marked `model` must reach the model (whether the model then gets it right
is checked at home, M3-3).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from jarvis.llm.conversation import Conversation
from jarvis.llm.intent_router import IntentRouter, SpeakIntent, StopIntent, ToolIntent
from jarvis.llm.ollama_client import OllamaClient
from jarvis.tools.catalogue import _local_tool_classes
from jarvis.tools.local.open_targets import make_installed_app_planner
from tests.llm.test_intent_router import FakeOllama, _Conv

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "regression_phrases.yaml"
_OUTCOMES = ("tool", "speak", "stop", "model")
_KEYS = {"say", "args", *_OUTCOMES}


def _load() -> list[dict[str, Any]]:
    return yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))


_PHRASES = _load()
_TOOLS = {cls.name: cls for cls in _local_tool_classes()}


def test_fixture_is_well_formed():
    seen: set[str] = set()
    for entry in _PHRASES:
        say = entry.get("say")
        assert isinstance(say, str) and say, entry
        assert say not in seen, f"duplicate phrase {say!r}"
        seen.add(say)
        assert set(entry) <= _KEYS, f"{say!r}: unknown keys {set(entry) - _KEYS}"
        outcomes = [k for k in _OUTCOMES if k in entry]
        assert len(outcomes) == 1, f"{say!r}: needs exactly one of {_OUTCOMES}"
        if "args" in entry:
            assert "tool" in entry, f"{say!r}: args without a tool"
        if "tool" in entry:
            tool = _TOOLS.get(entry["tool"])
            assert tool is not None, f"{say!r}: unknown tool {entry['tool']!r}"
            tool.args_schema.model_validate(entry.get("args", {}))


async def _route(phrase: str) -> tuple[Any, bool]:
    """The router's first intent for `phrase`, and whether the model ran."""
    llm = FakeOllama()
    router = IntentRouter(
        llm=cast(OllamaClient, llm),
        conversation=cast(Conversation, _Conv()),
        time_provider=lambda: datetime.datetime(2026, 11, 6, 15, 0),
        # No installed programs: "открой X" for a program stays open_app.
        open_target_planner=make_installed_app_planner(lambda: {}),
    )
    intents = [item async for item in router.route(phrase)]
    return (intents[0] if intents else None), bool(llm.stream_calls)


@pytest.mark.parametrize("entry", _PHRASES, ids=[e["say"] for e in _PHRASES])
async def test_phrase(entry: dict[str, Any]):
    intent, used_model = await _route(entry["say"])
    if entry.get("model"):
        assert used_model, f"a pattern took it: {intent!r}"
        return
    assert not used_model, "went to the model"
    if "tool" in entry:
        assert isinstance(intent, ToolIntent), intent
        assert (intent.tool_name, intent.args) == (entry["tool"], entry.get("args", {}))
    elif entry.get("speak"):
        assert isinstance(intent, SpeakIntent), intent
    elif entry.get("stop"):
        assert isinstance(intent, StopIntent), intent
