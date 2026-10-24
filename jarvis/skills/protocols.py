"""Protocol engine — user-defined voice macro chains.

A *protocol* is a named sequence of steps the user creates with their
voice.  Example: "Create protocol Party — turn on music and set volume
to max."  Later the user says "Party" and JARVIS executes the chain.

Storage
-------
SQLite database at ``~/.jarvis/protocols.db`` (one table, simple schema).
Each protocol row stores:

  - name (TEXT UNIQUE, case-insensitive lookup via COLLATE NOCASE)
  - steps (JSON array of step dicts)
  - enabled (INTEGER 0/1, default 1)
  - created_at / updated_at (ISO timestamps)

Step format::

    {
        "tool_name": "volume",
        "args": {"action": "up", "amount": 10},
        "delay_after": 0.0,
        "condition": null
    }

Two creation modes
------------------
1. **Simple regex** — covers the 80 % case where the user says something
   like "включи музыку и поставь звук на максимум".  A set of pattern
   rules map common phrases to tool+args.

2. **LLM fallback** — for anything the regex doesn't cover, the engine
   passes the description to the LLM, which returns a JSON array of
   steps validated against the tool registry.

Execution
---------
Steps run sequentially by default.  Each step is dispatched through
``ToolRegistry.execute()`` so confirmation, risk levels, and
enable/disable all apply.  A ``delay_after`` field (seconds) allows
spacing between steps.  A ``condition`` field (future) allows
if-checks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

_PROTOCOL_NAME_RE = re.compile(r"^[\w\s\-]{1,64}$", re.UNICODE)


@dataclass
class ProtocolStep:
    """One step in a protocol chain."""

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    delay_after: float = 0.0
    condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args": self.args,
            "delay_after": self.delay_after,
            "condition": self.condition,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProtocolStep:
        return cls(
            tool_name=d["tool_name"],
            args=d.get("args", {}),
            delay_after=float(d.get("delay_after", 0.0)),
            condition=d.get("condition"),
        )


@dataclass
class Protocol:
    """A complete user-defined protocol."""

    name: str
    steps: list[ProtocolStep]
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ProtocolExecutionResult:
    """Outcome of running a protocol."""

    protocol_name: str
    success: bool
    steps_completed: int
    steps_total: int
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Simple phrase → step mapping (regex mode)
# ---------------------------------------------------------------------------

# Maps (language-agnostic) phrase patterns → (tool_name, args_builder).
# These cover the most common voice phrases.  Order matters: first match
# wins.

_SIMPLE_STEP_PATTERNS: list[
    tuple[re.Pattern[str], str, dict[str, Any] | None]
] = [
    # --- Music / media ---
    (re.compile(r"(?:включи|play|start|turn\s+on)\s+(?:музыку|music)", re.I),
     "media_control", {"action": "play"}),
    (re.compile(r"(?:выключи|stop|pause|turn\s+off)\s+(?:музыку|music)", re.I),
     "media_control", {"action": "pause"}),
    (re.compile(r"(?:следующ(?:ий|ая|ее)|next)\s+(?:трек|track|song|песн[яю])", re.I),
     "media_control", {"action": "next_track"}),
    (re.compile(r"(?:предыдущ(?:ий|ая|ее)|prev(?:ious)?)\s+(?:трек|track|song|песн[яю])", re.I),
     "media_control", {"action": "prev_track"}),
    (re.compile(r"пауза|pause", re.I),
     "media_control", {"action": "pause"}),

    # --- Volume ---
    (re.compile(r"(?:звук|volume|громкость)\s+(?:на\s+)?(?:максимум|max(?:imum)?)", re.I),
     "volume", {"action": "up", "amount": 20}),
    (re.compile(r"(?:звук|volume|громкость)\s+(?:на\s+)?(?:минимум|min(?:imum)?)", re.I),
     "volume", {"action": "down", "amount": 20}),
    (re.compile(r"(?:звук|volume|громкость)\s+(?:громче|up|выше|больше)", re.I),
     "volume", {"action": "up", "amount": 5}),
    (re.compile(r"(?:звук|volume|громкость)\s+(?:тише|down|ниже|меньше)", re.I),
     "volume", {"action": "down", "amount": 5}),
    (re.compile(r"(?:выключи|mute|замьюти)\s+(?:звук|volume|громкость)", re.I),
     "volume", {"action": "mute"}),
    (re.compile(r"(?:включи|unmute)\s+(?:звук|volume|громкость)", re.I),
     "volume", {"action": "unmute"}),

    # --- Apps ---
    (re.compile(r"(?:открой|open|launch|запусти)\s+(.+)", re.I),
     "open_app", None),  # args built dynamically from capture group
    (re.compile(r"(?:закрой|close)\s+(.+)", re.I),
     "close_app", None),

    # --- Screenshot ---
    (re.compile(r"(?:сделай\s+)?(?:скриншот|screenshot|снимок\s+экрана)", re.I),
     "screenshot", {}),

    # --- Lock screen ---
    (re.compile(r"(?:заблокируй|lock)\s+(?:экран|screen|компьютер|computer)", re.I),
     "lock_screen", {}),

    # --- Timer ---
    (re.compile(
        r"(?:поставь|set|start)\s+(?:таймер|timer)\s+"
        r"(?:на\s+|for\s+)?(\d+)\s*(?:минут[а-я]*|мин|minutes?|min)",
        re.I,
    ), "set_timer", None),  # dynamic
]


def _parse_simple_steps(description: str) -> list[ProtocolStep] | None:
    """Try to parse a protocol description into steps using simple patterns.

    The description is split on conjunctions ('и', 'and', 'потом',
    'then', ',') and each fragment is matched against
    ``_SIMPLE_STEP_PATTERNS``.

    Returns None if any fragment fails to match — caller should fall
    back to LLM parsing.
    """
    # Split on conjunctions
    fragments = re.split(
        r"\s*(?:,\s*(?:и\s+|and\s+)?|(?:\s+и\s+|\s+and\s+|\s+потом\s+|\s+then\s+|\s+а\s+также\s+))\s*",
        description.strip(),
    )
    fragments = [f.strip() for f in fragments if f.strip()]

    if not fragments:
        return None

    steps: list[ProtocolStep] = []
    for frag in fragments:
        matched = False
        for pattern, tool_name, static_args in _SIMPLE_STEP_PATTERNS:
            m = pattern.search(frag)
            if m is None:
                continue

            if static_args is not None:
                args = dict(static_args)
            elif tool_name == "open_app":
                args = {"name": m.group(1).strip()}
            elif tool_name == "close_app":
                args = {"name": m.group(1).strip()}
            elif tool_name == "set_timer":
                args = {"minutes": int(m.group(1))}
            else:
                args = {}

            steps.append(ProtocolStep(tool_name=tool_name, args=args))
            matched = True
            break

        if not matched:
            return None  # fallback to LLM

    return steps if steps else None


# ---------------------------------------------------------------------------
# LLM-based step parsing
# ---------------------------------------------------------------------------

_LLM_PARSE_PROMPT = """\
You are parsing a voice command into a sequence of tool actions.
The user wants to create a macro (protocol) that executes multiple tool actions in sequence.

Available tools (tool_name → description):
{tool_list}

User's description of the protocol steps:
"{description}"

Return a JSON array of steps. Each step is an object with:
- "tool_name": string (must be one of the available tools)
- "args": object (arguments for the tool)
- "delay_after": number (seconds to wait after this step, default 0)

Return ONLY the JSON array, no other text.
Example: [{"tool_name": "media_control", "args": {"action": "play"}, "delay_after": 0}]
"""


async def _parse_steps_with_llm(
    description: str,
    ollama_client: Any,
    registry: ToolRegistry | None = None,
) -> list[ProtocolStep] | None:
    """Use the LLM to parse a complex protocol description into steps."""
    if ollama_client is None:
        return None

    # Build tool list from registry
    tool_list_str = ""
    if registry is not None:
        for tool in registry.list_enabled():
            tool_list_str += f"- {tool.name}: {tool.description}\n"
    else:
        tool_list_str = "(no tools available)"

    prompt = _LLM_PARSE_PROMPT.format(
        tool_list=tool_list_str,
        description=description,
    )

    try:
        messages = [{"role": "user", "content": prompt}]
        chunks: list[str] = []
        async for chunk in ollama_client.stream_chat(messages):
            if chunk.content:
                chunks.append(chunk.content)
        raw_text = "".join(chunks).strip()

        # Extract JSON array from the response
        # Try direct parse first, then look for [...] in text
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw_text, re.DOTALL)
            if m is None:
                log.warning("LLM did not return valid JSON for protocol steps")
                return None
            data = json.loads(m.group(0))

        if not isinstance(data, list):
            return None

        steps = []
        for item in data:
            if not isinstance(item, dict) or "tool_name" not in item:
                continue
            steps.append(ProtocolStep.from_dict(item))

        return steps if steps else None

    except Exception:
        log.exception("LLM protocol parsing failed")
        return None


# ---------------------------------------------------------------------------
# ProtocolEngine — SQLite storage + execution
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    """Default database path: <data_dir>/protocols.db.

    Single data-dir resolver (jarvis.paths); resolved at call time."""
    from jarvis.paths import data_path

    return data_path("protocols.db")


class ProtocolEngine:
    """Manages user-defined voice protocols.

    Thread-safe: the SQLite connection uses check_same_thread=False and
    all writes are serialised through a single sync connection.  Async
    execution dispatches through the ToolRegistry.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        registry: ToolRegistry | None = None,
        ollama_client: Any | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path else _default_db_path()
        self._registry = registry
        self._ollama_client = ollama_client
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    # -- database setup ------------------------------------------------

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS protocols (
                name       TEXT    PRIMARY KEY COLLATE NOCASE,
                steps      TEXT    NOT NULL DEFAULT '[]',
                enabled    INTEGER NOT NULL DEFAULT 1,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- CRUD ----------------------------------------------------------

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def create_protocol(
        self,
        name: str,
        description: str,
    ) -> Protocol:
        """Create a protocol from a voice description.

        Tries simple regex parsing first; falls back to LLM if needed.

        Raises:
            ValueError: invalid name or no steps could be parsed.
        """
        name = name.strip()
        if not name or not _PROTOCOL_NAME_RE.match(name):
            raise ValueError(
                f"Invalid protocol name: {name!r}. "
                "Use 1-64 alphanumeric/space/dash characters."
            )

        # Check for duplicates
        if self.get_protocol(name) is not None:
            raise ValueError(f"Protocol {name!r} already exists.")

        # Try simple regex parsing first
        steps = _parse_simple_steps(description)

        # Fallback to LLM
        if steps is None:
            steps = await _parse_steps_with_llm(
                description, self._ollama_client, self._registry,
            )

        if not steps:
            raise ValueError(
                "Could not parse protocol steps from description. "
                "Try rephrasing or listing actions explicitly."
            )

        now = self._now_iso()
        protocol = Protocol(
            name=name,
            steps=steps,
            enabled=True,
            created_at=now,
            updated_at=now,
        )

        steps_json = json.dumps(
            [s.to_dict() for s in steps], ensure_ascii=False,
        )
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO protocols (name, steps, enabled, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?)",
            (name, steps_json, now, now),
        )
        self._conn.commit()
        return protocol

    def get_protocol(self, name: str) -> Protocol | None:
        """Fetch a protocol by name (case-insensitive)."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT name, steps, enabled, created_at, updated_at "
            "FROM protocols WHERE name = ? COLLATE NOCASE",
            (name.strip(),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_protocol(row)

    def list_protocols(self, *, enabled_only: bool = False) -> list[Protocol]:
        """List all protocols, optionally filtering to enabled only."""
        assert self._conn is not None
        query = "SELECT name, steps, enabled, created_at, updated_at FROM protocols"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name COLLATE NOCASE"
        rows = self._conn.execute(query).fetchall()
        return [self._row_to_protocol(r) for r in rows]

    def delete_protocol(self, name: str) -> bool:
        """Delete a protocol by name. Returns True if it existed."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "DELETE FROM protocols WHERE name = ? COLLATE NOCASE",
            (name.strip(),),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a protocol. Returns True if found."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "UPDATE protocols SET enabled = ?, updated_at = ? "
            "WHERE name = ? COLLATE NOCASE",
            (1 if enabled else 0, self._now_iso(), name.strip()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def update_steps(
        self, name: str, steps: list[ProtocolStep],
    ) -> bool:
        """Replace the steps of an existing protocol. Returns True if found."""
        assert self._conn is not None
        steps_json = json.dumps(
            [s.to_dict() for s in steps], ensure_ascii=False,
        )
        cursor = self._conn.execute(
            "UPDATE protocols SET steps = ?, updated_at = ? "
            "WHERE name = ? COLLATE NOCASE",
            (steps_json, self._now_iso(), name.strip()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # -- execution -----------------------------------------------------

    async def execute_protocol(self, name: str) -> ProtocolExecutionResult:
        """Execute all steps of a protocol sequentially.

        Each step is dispatched through the ToolRegistry so all risk /
        confirmation / enable gates apply.
        """
        protocol = self.get_protocol(name)
        if protocol is None:
            return ProtocolExecutionResult(
                protocol_name=name,
                success=False,
                steps_completed=0,
                steps_total=0,
                error=f"Protocol {name!r} not found.",
            )
        if not protocol.enabled:
            return ProtocolExecutionResult(
                protocol_name=name,
                success=False,
                steps_completed=0,
                steps_total=len(protocol.steps),
                error=f"Protocol {name!r} is disabled.",
            )
        if self._registry is None:
            return ProtocolExecutionResult(
                protocol_name=name,
                success=False,
                steps_completed=0,
                steps_total=len(protocol.steps),
                error="No tool registry available.",
            )

        results: list[dict[str, Any]] = []
        for i, step in enumerate(protocol.steps):
            try:
                result = await self._registry.execute(
                    step.tool_name, step.args,
                )
                results.append({
                    "step": i + 1,
                    "tool": step.tool_name,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                })
                if not result.success:
                    return ProtocolExecutionResult(
                        protocol_name=name,
                        success=False,
                        steps_completed=i,
                        steps_total=len(protocol.steps),
                        results=results,
                        error=f"Step {i + 1} ({step.tool_name}) failed: {result.error}",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Protocol step %d failed", i + 1)
                results.append({
                    "step": i + 1,
                    "tool": step.tool_name,
                    "success": False,
                    "error": str(exc),
                })
                return ProtocolExecutionResult(
                    protocol_name=name,
                    success=False,
                    steps_completed=i,
                    steps_total=len(protocol.steps),
                    results=results,
                    error=f"Step {i + 1} ({step.tool_name}) crashed: {exc}",
                )

            # Delay between steps
            if step.delay_after > 0:
                await asyncio.sleep(step.delay_after)

        return ProtocolExecutionResult(
            protocol_name=name,
            success=True,
            steps_completed=len(protocol.steps),
            steps_total=len(protocol.steps),
            results=results,
        )

    # -- matching (for IntentRouter integration) -----------------------

    def match_protocol(self, utterance: str) -> Protocol | None:
        """Check if an utterance matches an enabled protocol name.

        Comparison is case-insensitive and ignores leading/trailing
        whitespace.  Returns the Protocol if matched, None otherwise.
        """
        normalized = utterance.strip().lower()
        if not normalized:
            return None
        for proto in self.list_protocols(enabled_only=True):
            if proto.name.lower() == normalized:
                return proto
        return None

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_protocol(row: tuple) -> Protocol:
        name, steps_json, enabled, created_at, updated_at = row
        steps_data = json.loads(steps_json) if steps_json else []
        steps = [ProtocolStep.from_dict(s) for s in steps_data]
        return Protocol(
            name=name,
            steps=steps,
            enabled=bool(enabled),
            created_at=created_at or "",
            updated_at=updated_at or "",
        )
