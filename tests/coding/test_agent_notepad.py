"""Tests for AgentNotepad — persistent notes between coding steps.

Verifies:
  - Adding notes (deduplication, stripping)
  - Adding decisions (deduplication)
  - Remembering files (replace on re-remember)
  - forget_file removes entries
  - clear resets everything
  - to_context serialisation
  - Token-budget truncation
  - Introspection properties (note_count, is_empty, repr)
"""

from __future__ import annotations

import pytest

from jarvis.coding.agent_notepad import AgentNotepad

# ---------------------------------------------------------------------------
# Basic mutations
# ---------------------------------------------------------------------------

class TestAddNote:
    def test_add_and_retrieve(self):
        pad = AgentNotepad()
        pad.add_note("Flask uses Blueprints for routing")
        assert pad.note_count == 1
        ctx = pad.to_context()
        assert "Flask uses Blueprints" in ctx

    def test_duplicate_note_ignored(self):
        pad = AgentNotepad()
        pad.add_note("same note")
        pad.add_note("same note")
        assert pad.note_count == 1

    def test_empty_note_ignored(self):
        pad = AgentNotepad()
        pad.add_note("")
        pad.add_note("   ")
        assert pad.note_count == 0

    def test_whitespace_stripped(self):
        pad = AgentNotepad()
        pad.add_note("  padded  ")
        assert pad.note_count == 1
        assert "padded" in pad.to_context()


class TestAddDecision:
    def test_add_decision(self):
        pad = AgentNotepad()
        pad.add_decision("Use JWT for authentication")
        assert pad.decision_count == 1
        ctx = pad.to_context()
        assert "JWT" in ctx
        assert "[Decisions]" in ctx

    def test_duplicate_decision_ignored(self):
        pad = AgentNotepad()
        pad.add_decision("Use PostgreSQL")
        pad.add_decision("Use PostgreSQL")
        assert pad.decision_count == 1

    def test_empty_decision_ignored(self):
        pad = AgentNotepad()
        pad.add_decision("")
        assert pad.decision_count == 0


class TestRememberFile:
    def test_remember_and_retrieve(self):
        pad = AgentNotepad()
        pad.remember_file("models.py", "class User with fields: id, email")
        assert pad.file_count == 1
        ctx = pad.to_context()
        assert "models.py" in ctx
        assert "User" in ctx

    def test_replace_on_re_remember(self):
        pad = AgentNotepad()
        pad.remember_file("models.py", "old summary")
        pad.remember_file("models.py", "new summary")
        assert pad.file_count == 1
        ctx = pad.to_context()
        assert "new summary" in ctx
        assert "old summary" not in ctx

    def test_backslash_normalised(self):
        pad = AgentNotepad()
        pad.remember_file("src\\models.py", "normalised path")
        assert pad.file_count == 1
        ctx = pad.to_context()
        assert "src/models.py" in ctx


class TestForgetFile:
    def test_forget_existing(self):
        pad = AgentNotepad()
        pad.remember_file("x.py", "something")
        pad.forget_file("x.py")
        assert pad.file_count == 0

    def test_forget_nonexistent_no_error(self):
        pad = AgentNotepad()
        pad.forget_file("nope.py")  # should not raise
        assert pad.file_count == 0


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_resets_everything(self):
        pad = AgentNotepad()
        pad.add_note("note")
        pad.add_decision("decision")
        pad.remember_file("f.py", "summary")
        assert not pad.is_empty
        pad.clear()
        assert pad.is_empty
        assert pad.note_count == 0
        assert pad.decision_count == 0
        assert pad.file_count == 0
        assert pad.to_context() == ""


# ---------------------------------------------------------------------------
# to_context serialisation
# ---------------------------------------------------------------------------

class TestToContext:
    def test_empty_notepad(self):
        pad = AgentNotepad()
        assert pad.to_context() == ""

    def test_all_sections_present(self):
        pad = AgentNotepad()
        pad.add_decision("D1")
        pad.remember_file("f.py", "summary")
        pad.add_note("N1")
        ctx = pad.to_context()
        assert "[Decisions]" in ctx
        assert "[Files explored]" in ctx
        assert "[Notes]" in ctx

    def test_decisions_before_notes(self):
        pad = AgentNotepad()
        pad.add_note("note first")
        pad.add_decision("decision second")
        ctx = pad.to_context()
        # Decisions should appear before notes.
        assert ctx.index("[Decisions]") < ctx.index("[Notes]")

    def test_truncation(self):
        pad = AgentNotepad()
        for i in range(200):
            pad.add_note(f"Very long note number {i} with extra padding text to fill tokens")
        ctx = pad.to_context(max_tokens=50)  # ~200 chars
        assert len(ctx) <= 210  # some slack for the truncation marker
        assert ctx.endswith("...")

    def test_small_budget_still_returns_something(self):
        pad = AgentNotepad()
        pad.add_decision("important")
        ctx = pad.to_context(max_tokens=10)
        assert len(ctx) > 0


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

class TestIntrospection:
    def test_is_empty(self):
        pad = AgentNotepad()
        assert pad.is_empty
        pad.add_note("x")
        assert not pad.is_empty

    def test_repr(self):
        pad = AgentNotepad()
        pad.add_note("a")
        pad.add_decision("b")
        pad.remember_file("c.py", "d")
        r = repr(pad)
        assert "notes=1" in r
        assert "decisions=1" in r
        assert "files=1" in r

    def test_counts(self):
        pad = AgentNotepad()
        assert pad.note_count == 0
        assert pad.decision_count == 0
        assert pad.file_count == 0
        pad.add_note("n")
        pad.add_decision("d")
        pad.remember_file("f", "s")
        assert pad.note_count == 1
        assert pad.decision_count == 1
        assert pad.file_count == 1
