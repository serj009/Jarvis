"""Tests for IntentRouter × ProtocolEngine integration.

Verifies that:
  - Protocol names are checked BEFORE tool patterns in route()
  - A matched protocol yields ToolIntent("run_protocol", {"name": ...})
  - An unmatched name falls through to normal pattern/LLM routing
  - router works normally when protocol_engine is None (backward compat)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.llm.intent_router import IntentRouter, ToolIntent

# --- minimal fakes (same pattern as tests/llm/test_router*.py) ---------

def _fake_llm() -> MagicMock:
    llm = MagicMock()
    # stream_chat returns an async iterator of chunks
    async def _empty_stream(*a, **kw):
        return
        yield  # pragma: no cover — makes this an async gen
    llm.stream_chat = _empty_stream
    return llm


def _fake_conversation() -> MagicMock:
    conv = MagicMock()
    conv.add_user_turn = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    conv.add_assistant_turn = MagicMock()
    conv.clear_injected_context = MagicMock()
    return conv


def _fake_protocol_engine(protocols: dict[str, bool] | None = None):
    """Create a mock ProtocolEngine.

    protocols: {name: enabled} — if a name matches, returns a fake Protocol.
    """
    from jarvis.skills.protocols import Protocol, ProtocolStep

    engine = MagicMock()

    def _match(utterance: str):
        if protocols is None:
            return None
        normalized = utterance.strip().lower()
        for name, enabled in protocols.items():
            if name.lower() == normalized and enabled:
                return Protocol(
                    name=name,
                    steps=[ProtocolStep(tool_name="screenshot")],
                    enabled=True,
                )
        return None

    engine.match_protocol = MagicMock(side_effect=_match)
    return engine


# ======================================================================
# Tests
# ======================================================================

class TestProtocolRouterWiring:

    @pytest.mark.asyncio
    async def test_protocol_matched_yields_run_protocol(self):
        """A protocol name triggers ToolIntent('run_protocol', ...)."""
        engine = _fake_protocol_engine({"party": True})
        router = IntentRouter(
            llm=_fake_llm(),
            conversation=_fake_conversation(),
            protocol_engine=engine,
        )
        intents = []
        async for intent in router.route("Party"):
            intents.append(intent)
        assert len(intents) == 1
        assert isinstance(intents[0], ToolIntent)
        assert intents[0].tool_name == "run_protocol"
        assert intents[0].args["name"] == "party"

    @pytest.mark.asyncio
    async def test_no_protocol_falls_through(self):
        """When no protocol matches, routing continues normally."""
        engine = _fake_protocol_engine({"party": True})
        router = IntentRouter(
            llm=_fake_llm(),
            conversation=_fake_conversation(),
            protocol_engine=engine,
        )
        # "stop" is a stop pattern, not a protocol
        intents = []
        async for intent in router.route("stop"):
            intents.append(intent)
        # StopIntent or empty — NOT a protocol match
        for i in intents:
            if isinstance(i, ToolIntent):
                assert i.tool_name != "run_protocol"

    @pytest.mark.asyncio
    async def test_disabled_protocol_not_matched(self):
        """Disabled protocols should not be matched."""
        engine = _fake_protocol_engine({"party": False})
        router = IntentRouter(
            llm=_fake_llm(),
            conversation=_fake_conversation(),
            protocol_engine=engine,
        )
        intents = []
        async for intent in router.route("Party"):
            intents.append(intent)
        # Should NOT produce run_protocol
        for i in intents:
            if isinstance(i, ToolIntent):
                assert i.tool_name != "run_protocol"

    @pytest.mark.asyncio
    async def test_no_engine_backward_compat(self):
        """Router works fine when protocol_engine is None."""
        router = IntentRouter(
            llm=_fake_llm(),
            conversation=_fake_conversation(),
            protocol_engine=None,
        )
        # Should not crash, "stop" just yields StopIntent
        intents = []
        async for intent in router.route("stop"):
            intents.append(intent)
        # Just check it didn't crash

    @pytest.mark.asyncio
    async def test_protocol_checked_before_patterns(self):
        """A protocol named 'pause' should be matched BEFORE the media_control
        voice pattern for 'pause'."""
        engine = _fake_protocol_engine({"pause": True})
        router = IntentRouter(
            llm=_fake_llm(),
            conversation=_fake_conversation(),
            protocol_engine=engine,
        )
        intents = []
        async for intent in router.route("pause"):
            intents.append(intent)
        # Should be run_protocol, not media_control
        assert len(intents) == 1
        assert isinstance(intents[0], ToolIntent)
        assert intents[0].tool_name == "run_protocol"
