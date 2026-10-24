"""Persistent JSON storage for CodingAgent tasks and their step-by-step
progress.

Each task is a ``CodingTask`` dataclass with an ordered list of
``TaskStep`` entries.  ``TaskStorage`` serialises the list to a single
JSON file so that the Coding Panel sidebar can show history across
sessions.

Thread-safety
-------------
All public methods acquire a threading lock, so the audio thread (which
drives the CodingAgent) and the Qt main thread (which reads for display)
can share one ``TaskStorage`` instance without corruption.

File format
-----------
The file is a JSON array of task dicts, newest-first.  Each dict carries
every field of ``CodingTask`` and nested ``TaskStep`` objects, with
datetimes stored as ISO-8601 strings (UTC).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_FILE = "coding_tasks.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaskStep:
    """One step inside a coding task's execution plan."""

    title: str = ""
    detail: str = ""
    status: str = "pending"  # "pending" | "running" | "done" | "error"
    files: list[str] = field(default_factory=list)
    output: str = ""
    started_at: str | None = None   # ISO-8601 or None
    completed_at: str | None = None


@dataclass
class CodingTask:
    """Full record of a single coding task."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    language: str = "python"
    status: str = "running"  # "running" | "done" | "failed" | "cancelled"
    steps: list[TaskStep] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None
    workspace_path: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    # -- helpers -----------------------------------------------------------

    @property
    def running_step_index(self) -> int | None:
        """Index of the first step with status ``'running'``, or *None*."""
        for i, s in enumerate(self.steps):
            if s.status == "running":
                return i
        return None

    @property
    def progress(self) -> tuple[int, int]:
        """``(done_count, total)``."""
        done = sum(1 for s in self.steps if s.status == "done")
        return done, len(self.steps)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _task_to_dict(task: CodingTask) -> dict:
    return asdict(task)


def _task_from_dict(d: dict) -> CodingTask:
    steps = [TaskStep(**s) for s in d.pop("steps", [])]
    return CodingTask(steps=steps, **d)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TaskStorage:
    """JSON-file persistent storage for :class:`CodingTask` objects.

    Parameters
    ----------
    path:
        Path to the JSON file.  Parent directories are created
        automatically.  Defaults to ``~/.jarvis/coding_tasks.json``.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            # Single data-dir resolver (jarvis.paths); layout unchanged:
            # <data_dir>/<_DEFAULT_FILE>. Resolved at call time.
            from jarvis.paths import data_path
            path = data_path(_DEFAULT_FILE)
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # -- CRUD --------------------------------------------------------------

    def save(self, task: CodingTask) -> None:
        """Insert or update *task* (matched by ``task.id``)."""
        with self._lock:
            tasks = self._read_all_unlocked()
            found = False
            for i, t in enumerate(tasks):
                if t.id == task.id:
                    tasks[i] = task
                    found = True
                    break
            if not found:
                tasks.insert(0, task)
            self._write_all_unlocked(tasks)

    def load_all(self) -> list[CodingTask]:
        """Return every stored task, newest-first."""
        with self._lock:
            return self._read_all_unlocked()

    def get(self, task_id: str) -> CodingTask | None:
        """Return a single task by id, or *None*."""
        with self._lock:
            for t in self._read_all_unlocked():
                if t.id == task_id:
                    return t
        return None

    def delete(self, task_id: str) -> bool:
        """Remove a task.  Returns ``True`` if the task existed."""
        with self._lock:
            tasks = self._read_all_unlocked()
            before = len(tasks)
            tasks = [t for t in tasks if t.id != task_id]
            if len(tasks) == before:
                return False
            self._write_all_unlocked(tasks)
            return True

    # -- internal IO -------------------------------------------------------

    def _read_all_unlocked(self) -> list[CodingTask]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [_task_from_dict(d) for d in data]
        except Exception:
            log.warning("corrupt task storage at %s; returning empty", self._path)
            return []

    def _write_all_unlocked(self, tasks: list[CodingTask]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([_task_to_dict(t) for t in tasks], indent=2),
            encoding="utf-8",
        )
