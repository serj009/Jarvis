"""Tests for jarvis.core.personalization: PersonalizationContext."""

from __future__ import annotations

from jarvis.core.personalization import PersonalizationContext


def test_empty_creates_all_none():
    """PersonalizationContext.empty() must produce a context with every field None."""
    ctx = PersonalizationContext.empty()
    assert ctx.memory_store is None
    assert ctx.relation_store is None
    assert ctx.tool_examples is None
    assert ctx.personal_aliases is None
    assert ctx.user_profile is None
    assert ctx.usage_stats is None
    assert ctx.game_mode is None


def test_default_constructor_all_none():
    """Bare PersonalizationContext() is equivalent to empty()."""
    ctx = PersonalizationContext()
    assert ctx.memory_store is None
    assert ctx.relation_store is None
    assert ctx.tool_examples is None
    assert ctx.personal_aliases is None
    assert ctx.user_profile is None
    assert ctx.usage_stats is None
    assert ctx.game_mode is None


def test_fields_are_assignable():
    """Fields set at construction are reachable on the instance."""
    sentinel = object()
    ctx = PersonalizationContext(memory_store=sentinel, user_profile=sentinel)
    assert ctx.memory_store is sentinel
    assert ctx.user_profile is sentinel
    assert ctx.tool_examples is None  # unset stays None


def test_fields_are_mutable():
    """Fields can be reassigned after construction (game_mode is set post-init)."""
    ctx = PersonalizationContext.empty()
    sentinel = object()
    ctx.game_mode = sentinel
    assert ctx.game_mode is sentinel


def test_is_dataclass():
    """PersonalizationContext is a dataclass (supports field enumeration)."""
    import dataclasses
    assert dataclasses.is_dataclass(PersonalizationContext)
    field_names = {f.name for f in dataclasses.fields(PersonalizationContext)}
    expected = {
        "memory_store", "relation_store", "tool_examples",
        "personal_aliases", "user_profile", "usage_stats", "game_mode",
    }
    assert field_names == expected


# ---------------------------------------------------------------------------
# record_tool_success -- the learning path for snap / one-shot tool calls
# ---------------------------------------------------------------------------


class _FakeToolExamples:
    def __init__(self):
        self.calls = []

    def record_success(self, utterance, tool_name, args):
        self.calls.append((utterance, tool_name, args))


class _FakeUsageStats:
    def __init__(self):
        self.tools = []
        self.apps = []

    def record_tool_use(self, tool_name):
        self.tools.append(tool_name)

    def record_app_use(self, app_name):
        self.apps.append(app_name)


class _FakeAliases:
    def __init__(self):
        self.learned = []

    def learn(self, alias, target):
        self.learned.append((alias, target))


def test_record_tool_success_records_example_and_usage():
    from jarvis.core.personalization import record_tool_success

    ex, us = _FakeToolExamples(), _FakeUsageStats()
    ctx = PersonalizationContext(tool_examples=ex, usage_stats=us)

    record_tool_success(ctx, "take a screenshot", "screenshot", {})

    assert ex.calls == [("take a screenshot", "screenshot", {})]
    assert us.tools == ["screenshot"]
    assert us.apps == []  # not open_app -> no app recorded


def test_record_tool_success_records_app_use_for_open_app():
    from jarvis.core.personalization import record_tool_success

    us = _FakeUsageStats()
    ctx = PersonalizationContext(usage_stats=us)

    record_tool_success(ctx, "open chrome", "open_app", {"name": "chrome"})

    assert us.tools == ["open_app"]
    assert us.apps == ["chrome"]


def test_record_tool_success_learns_personal_alias():
    from jarvis.core.personalization import record_tool_success

    al = _FakeAliases()
    ctx = PersonalizationContext(personal_aliases=al)

    # Generic word "браузер" in utterance, specific app arg "chrome".
    record_tool_success(ctx, "открой браузер", "open_app", {"name": "chrome"})

    assert al.learned == [("браузер", "chrome")]


def test_record_tool_success_no_alias_without_generic_word():
    from jarvis.core.personalization import record_tool_success

    al = _FakeAliases()
    ctx = PersonalizationContext(personal_aliases=al)

    record_tool_success(ctx, "открой chrome", "open_app", {"name": "chrome"})

    assert al.learned == []  # no generic word -> nothing learned


def test_record_tool_success_all_none_is_noop():
    """Empty context must not raise -- every store is optional."""
    from jarvis.core.personalization import record_tool_success

    record_tool_success(PersonalizationContext.empty(), "hi", "any_tool", {})


def test_record_tool_success_is_non_fatal_on_store_error():
    """A raising store must not propagate -- learning is best-effort."""
    from jarvis.core.personalization import record_tool_success

    class _Boom:
        def record_success(self, *a):
            raise RuntimeError("disk full")

    ctx = PersonalizationContext(tool_examples=_Boom())
    # Must not raise.
    record_tool_success(ctx, "hi", "any_tool", {})
