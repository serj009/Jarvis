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
