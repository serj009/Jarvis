"""Agent notepad — persistent notes between coding steps.

The coding model has limited context.  Between steps, it "forgets" what
it learned.  The notepad stores structured notes that are injected into
every prompt, keeping the model oriented.

Three kinds of entries:
  * **Notes** — free-form observations the agent wants to remember.
  * **Decisions** — architectural decisions ("we use JWT for auth").
  * **File summaries** — per-file one-liners ("models.py: class User with
    fields id, email, password_hash, created_at").

The ``to_context`` method serialises these into a compact string that fits
within a token budget.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

# Rough estimate: 1 token ≈ 4 characters for English text.
_CHARS_PER_TOKEN: int = 4


@dataclass
class AgentNotepad:
    """Persistent notepad that bridges LLM context windows.

    Usage::

        pad = AgentNotepad()
        pad.add_note("User wants Flask + SQLAlchemy")
        pad.add_decision("Use JWT for authentication")
        pad.remember_file("models.py", "class User(id, email, password_hash)")

        # Inject into the LLM prompt:
        context_block = pad.to_context(max_tokens=500)
    """

    _notes: list[str] = field(default_factory=list)
    _decisions: list[str] = field(default_factory=list)
    _file_summaries: dict[str, str] = field(default_factory=dict)

    # -- mutations ---------------------------------------------------------

    def add_note(self, note: str) -> None:
        """Add a free-form note."""
        stripped = note.strip()
        if stripped and stripped not in self._notes:
            self._notes.append(stripped)

    def add_decision(self, decision: str) -> None:
        """Record an architectural decision."""
        stripped = decision.strip()
        if stripped and stripped not in self._decisions:
            self._decisions.append(stripped)

    def remember_file(self, path: str, summary: str) -> None:
        """Record what we learned about a file.

        If the same *path* is remembered again, the summary is **replaced**
        (latest knowledge wins).
        """
        normalised = path.replace("\\", "/").strip()
        self._file_summaries[normalised] = summary.strip()

    def forget_file(self, path: str) -> None:
        """Remove a file summary (e.g. after the file is deleted)."""
        normalised = path.replace("\\", "/").strip()
        self._file_summaries.pop(normalised, None)

    def clear(self) -> None:
        """Reset the notepad for a new task."""
        self._notes.clear()
        self._decisions.clear()
        self._file_summaries.clear()

    # -- serialisation -----------------------------------------------------

    def to_context(self, max_tokens: int = 500) -> str:
        """Generate a context string for injection into an LLM prompt.

        Returns an empty string when the notepad is empty.  The output is
        truncated to approximately *max_tokens* tokens (estimated via
        character count).
        """
        if not self._notes and not self._decisions and not self._file_summaries:
            return ""

        max_chars = max_tokens * _CHARS_PER_TOKEN
        sections: list[str] = []

        if self._decisions:
            lines = ["[Decisions]"] + [f"• {d}" for d in self._decisions]
            sections.append("\n".join(lines))

        if self._file_summaries:
            lines = ["[Files explored]"]
            for path, summary in self._file_summaries.items():
                lines.append(f"• {path}: {summary}")
            sections.append("\n".join(lines))

        if self._notes:
            lines = ["[Notes]"] + [f"• {n}" for n in self._notes]
            sections.append("\n".join(lines))

        full = "\n\n".join(sections)

        if len(full) <= max_chars:
            return full

        # Truncation: trim notes first, then file summaries.
        return self._truncate(max_chars)

    def _truncate(self, max_chars: int) -> str:
        """Build a truncated context, prioritising decisions > files > notes."""
        parts: list[str] = []

        if self._decisions:
            block = "[Decisions]\n" + "\n".join(f"• {d}" for d in self._decisions)
            parts.append(block)

        if self._file_summaries:
            lines = ["[Files explored]"]
            for path, summary in self._file_summaries.items():
                lines.append(f"• {path}: {summary}")
            parts.append("\n".join(lines))

        if self._notes:
            parts.append("[Notes]\n" + "\n".join(f"• {n}" for n in self._notes))

        result = "\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."
        return result

    # -- introspection -----------------------------------------------------

    @property
    def note_count(self) -> int:
        return len(self._notes)

    @property
    def decision_count(self) -> int:
        return len(self._decisions)

    @property
    def file_count(self) -> int:
        return len(self._file_summaries)

    @property
    def is_empty(self) -> bool:
        return not self._notes and not self._decisions and not self._file_summaries

    def __repr__(self) -> str:
        return (
            f"AgentNotepad(notes={len(self._notes)}, "
            f"decisions={len(self._decisions)}, "
            f"files={len(self._file_summaries)})"
        )
