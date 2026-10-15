"""Self-learning: store successful tool-call patterns as few-shot examples.

When a tool call succeeds (ToolResult.success=True), save the pattern:
  utterance → tool_name(args)

These examples are injected into the system prompt as few-shot examples,
teaching the model from its own experience.

Storage: simple JSON file at %APPDATA%/Jarvis/tool_examples.json
Format::

    {
        "open_app": [
            {"utterance": "открой хром", "args": {"app_name": "chrome"}, "count": 5,
             "last_used": "2026-10-14T15:30:00+00:00"},
            {"utterance": "open telegram", "args": {"app_name": "telegram"}, "count": 3,
             "last_used": "2026-10-13T10:00:00+00:00"},
        ],
        "get_weather": [
            {"utterance": "какая погода", "args": {}, "count": 12,
             "last_used": "2026-10-15T08:00:00+00:00"},
        ]
    }

Dedup: if the same utterance→tool already exists, increment count.
Each entry also tracks ``last_used`` (ISO timestamp of the most recent call).
Max examples per tool: 10 (keep highest count).
Max total: 100.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)

MAX_PER_TOOL = 10
MAX_TOTAL = 100


class ToolExampleStore:
    """Persistent store for successful tool-call examples."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = EncryptedJsonStore(path)
        self._data: dict[str, list[dict]] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data(self) -> dict[str, list[dict]]:
        """Read-only access to underlying data (for testing)."""
        return self._data

    def _load(self) -> None:
        try:
            self._data = self._store.load(default={})
        except Exception:
            log.warning(
                "[tool-examples] failed to load %s; starting fresh",
                self._path,
                exc_info=True,
            )
            self._data = {}

    def _save(self) -> None:
        try:
            self._store.save(self._data)
        except Exception:
            log.warning("[tool-examples] failed to save %s", self._path, exc_info=True)

    # -- public API --------------------------------------------------------

    def record_success(
        self, utterance: str, tool_name: str, args: dict,
    ) -> None:
        """Record a successful tool call for future reference.

        Safe to call from any context — never raises.
        """
        try:
            self._record_success_inner(utterance, tool_name, args)
        except Exception:
            log.warning("[tool-examples] record_success failed", exc_info=True)

    def _record_success_inner(
        self, utterance: str, tool_name: str, args: dict,
    ) -> None:
        examples = self._data.setdefault(tool_name, [])
        now = datetime.now(UTC).isoformat()

        # Dedup: check if an identical utterance already exists
        utterance_lower = utterance.lower().strip()
        for ex in examples:
            if ex.get("utterance", "").lower() == utterance_lower:
                ex["count"] = ex.get("count", 1) + 1
                ex["last_used"] = now
                self._save()
                return

        # New entry
        examples.append({
            "utterance": utterance_lower,
            "args": args,
            "count": 1,
            "last_used": now,
        })

        # Prune per-tool: keep top MAX_PER_TOOL by count
        if len(examples) > MAX_PER_TOOL:
            examples.sort(key=lambda x: x.get("count", 0), reverse=True)
            self._data[tool_name] = examples[:MAX_PER_TOOL]

        # Prune global: if total exceeds MAX_TOTAL, drop lowest-count
        # examples across all tools
        self._prune_global()

        self._save()

    def _prune_global(self) -> None:
        total = sum(len(exs) for exs in self._data.values())
        if total <= MAX_TOTAL:
            return
        # Collect all (tool, index, count) and sort by count ascending
        all_entries: list[tuple[str, int, int]] = []
        for tool_name, examples in self._data.items():
            for idx, ex in enumerate(examples):
                all_entries.append((tool_name, idx, ex.get("count", 0)))
        all_entries.sort(key=lambda x: x[2])

        # Drop lowest-count entries until within budget
        to_drop = total - MAX_TOTAL
        indices_to_drop: dict[str, set[int]] = {}
        for tool_name, idx, _count in all_entries[:to_drop]:
            indices_to_drop.setdefault(tool_name, set()).add(idx)

        for tool_name, drop_indices in indices_to_drop.items():
            self._data[tool_name] = [
                ex for i, ex in enumerate(self._data[tool_name])
                if i not in drop_indices
            ]
            # Clean up empty tools
            if not self._data[tool_name]:
                del self._data[tool_name]

    def get_examples_for_prompt(self, max_examples: int = 15) -> str:
        """Format top examples as text for system prompt injection.

        Returns an empty string when no examples are stored.
        """
        all_examples: list[tuple[int, str, dict]] = []
        for tool_name, examples in self._data.items():
            for ex in examples:
                all_examples.append((ex.get("count", 0), tool_name, ex))

        if not all_examples:
            return ""

        # Sort by count descending, take top N
        all_examples.sort(key=lambda x: x[0], reverse=True)

        lines: list[str] = []
        for _count, tool_name, ex in all_examples[:max_examples]:
            utterance = ex.get("utterance", "")
            args = ex.get("args", {})
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            lines.append(f'"{utterance}" → {tool_name}({args_str})')

        if not lines:
            return ""

        return "[Learned from past interactions:]\n" + "\n".join(lines)

    def example_count(self) -> int:
        """Total number of stored examples across all tools."""
        return sum(len(exs) for exs in self._data.values())
