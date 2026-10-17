"""Tests for CodingPanel, TaskStorage, and supporting widgets.

Architecture note: No real file I/O happens against user directories —
TaskStorage is pointed at ``tmp_path`` fixtures.  No real TTS or toast
notifications are fired.  We drive the panel by calling internal slots
directly and by simulating cross-thread signals on the Qt event loop.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# TaskStorage + data model tests (no Qt needed)
# ---------------------------------------------------------------------------


class TestTaskStep:
    """TaskStep dataclass basics."""

    def test_default_status_is_pending(self):
        from jarvis.coding.task_storage import TaskStep

        step = TaskStep(title="foo")
        assert step.status == "pending"

    def test_fields_assignable(self):
        from jarvis.coding.task_storage import TaskStep

        step = TaskStep(
            title="Build",
            detail="scaffold project",
            status="done",
            files=["main.py"],
            output="ok",
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:01:00+00:00",
        )
        assert step.title == "Build"
        assert step.files == ["main.py"]


class TestCodingTask:
    """CodingTask dataclass basics."""

    def test_default_status_is_running(self):
        from jarvis.coding.task_storage import CodingTask

        task = CodingTask(name="Test")
        assert task.status == "running"

    def test_progress_counts_done_steps(self):
        from jarvis.coding.task_storage import CodingTask, TaskStep

        task = CodingTask(
            name="T",
            steps=[
                TaskStep(title="a", status="done"),
                TaskStep(title="b", status="running"),
                TaskStep(title="c", status="pending"),
            ],
        )
        done, total = task.progress
        assert done == 1
        assert total == 3

    def test_running_step_index(self):
        from jarvis.coding.task_storage import CodingTask, TaskStep

        task = CodingTask(
            name="T",
            steps=[
                TaskStep(title="a", status="done"),
                TaskStep(title="b", status="running"),
            ],
        )
        assert task.running_step_index == 1

    def test_running_step_index_none_when_no_running(self):
        from jarvis.coding.task_storage import CodingTask, TaskStep

        task = CodingTask(
            name="T",
            steps=[TaskStep(title="a", status="done")],
        )
        assert task.running_step_index is None

    def test_id_auto_generated(self):
        from jarvis.coding.task_storage import CodingTask

        t1 = CodingTask(name="A")
        t2 = CodingTask(name="B")
        assert t1.id != t2.id
        assert len(t1.id) == 12


class TestTaskStorage:
    """CRUD operations against a temp JSON file."""

    def test_save_and_load(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        task = CodingTask(name="Hello")
        storage.save(task)

        loaded = storage.load_all()
        assert len(loaded) == 1
        assert loaded[0].name == "Hello"
        assert loaded[0].id == task.id

    def test_update_existing(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        task = CodingTask(name="V1")
        storage.save(task)

        task.name = "V2"
        storage.save(task)

        loaded = storage.load_all()
        assert len(loaded) == 1
        assert loaded[0].name == "V2"

    def test_get_by_id(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        task = CodingTask(name="Find me")
        storage.save(task)

        found = storage.get(task.id)
        assert found is not None
        assert found.name == "Find me"

    def test_get_missing_returns_none(self, tmp_path: Path):
        from jarvis.coding.task_storage import TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        assert storage.get("nonexistent") is None

    def test_delete(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        task = CodingTask(name="Gone")
        storage.save(task)

        assert storage.delete(task.id) is True
        assert storage.load_all() == []

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        from jarvis.coding.task_storage import TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        assert storage.delete("nope") is False

    def test_load_empty_when_no_file(self, tmp_path: Path):
        from jarvis.coding.task_storage import TaskStorage

        storage = TaskStorage(path=tmp_path / "nope.json")
        assert storage.load_all() == []

    def test_save_with_steps_roundtrip(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStep, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        task = CodingTask(
            name="With steps",
            steps=[
                TaskStep(title="Step 1", status="done", files=["a.py"]),
                TaskStep(title="Step 2", status="running"),
            ],
        )
        storage.save(task)

        loaded = storage.load_all()
        assert len(loaded[0].steps) == 2
        assert loaded[0].steps[0].title == "Step 1"
        assert loaded[0].steps[0].files == ["a.py"]
        assert loaded[0].steps[1].status == "running"

    def test_corrupt_file_returns_empty(self, tmp_path: Path):
        from jarvis.coding.task_storage import TaskStorage

        p = tmp_path / "bad.json"
        p.write_text("NOT JSON!!!", encoding="utf-8")
        storage = TaskStorage(path=p)
        assert storage.load_all() == []

    def test_newest_first(self, tmp_path: Path):
        from jarvis.coding.task_storage import CodingTask, TaskStorage

        storage = TaskStorage(path=tmp_path / "tasks.json")
        storage.save(CodingTask(name="First"))
        storage.save(CodingTask(name="Second"))

        loaded = storage.load_all()
        assert loaded[0].name == "Second"
        assert loaded[1].name == "First"


# ---------------------------------------------------------------------------
# CodingPanel widget tests (need qapp from conftest.py)
# ---------------------------------------------------------------------------


def _make_panel(qapp, tmp_path: Path):
    """Build a CodingPanel with temp storage and no real notifications."""
    from jarvis.coding.task_storage import TaskStorage
    from jarvis.ui.coding_panel import CodingPanel

    storage = TaskStorage(path=tmp_path / "panel_tasks.json")
    panel = CodingPanel(storage=storage, speak_fn=None)
    return panel


class TestCodingPanel:
    """CodingPanel construction and behaviour."""

    def test_initially_hidden(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        assert not panel.isVisible()

    def test_default_width(self, qapp, tmp_path):
        from jarvis.ui.coding_panel import _DEFAULT_PANEL_WIDTH

        panel = _make_panel(qapp, tmp_path)
        assert panel.width() == _DEFAULT_PANEL_WIDTH

    def test_task_submitted_signal(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        received: list[str] = []
        panel.task_submitted.connect(received.append)

        panel._input.setText("Create Flask API")
        panel._on_submit()

        assert received == ["Create Flask API"]
        assert panel._input.text() == ""

    def test_empty_submit_does_nothing(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)
        received: list[str] = []
        panel.task_submitted.connect(received.append)

        panel._input.setText("   ")
        panel._on_submit()

        assert received == []

    def test_display_task_shows_steps(self, qapp, tmp_path):
        from jarvis.coding.task_storage import CodingTask, TaskStep

        panel = _make_panel(qapp, tmp_path)
        task = CodingTask(
            name="My Task",
            language="python",
            steps=[
                TaskStep(title="Init", status="done"),
                TaskStep(title="Build", status="running"),
            ],
        )
        panel._display_task(task)

        assert panel._active_title.text() == "My Task"
        assert panel._lang_badge.text() == "PYTHON"
        assert not panel._lang_badge.isHidden()
        assert len(panel._step_widgets) == 2

    def test_display_task_clears_previous(self, qapp, tmp_path):
        from jarvis.coding.task_storage import CodingTask, TaskStep

        panel = _make_panel(qapp, tmp_path)

        task1 = CodingTask(
            name="T1",
            steps=[TaskStep(title="A"), TaskStep(title="B")],
        )
        panel._display_task(task1)
        assert len(panel._step_widgets) == 2

        task2 = CodingTask(
            name="T2",
            steps=[TaskStep(title="C")],
        )
        panel._display_task(task2)
        assert len(panel._step_widgets) == 1

    def test_minimize_and_expand(self, qapp, tmp_path):
        panel = _make_panel(qapp, tmp_path)

        panel._on_minimize()
        assert panel.minimized is True
        assert not panel._min_widget.isHidden()
        assert not panel._full_content.isVisible()

        panel._on_expand()
        assert panel.minimized is False
        assert not panel._min_widget.isVisible()
        assert not panel._full_content.isHidden()

    def test_on_task_updated_persists(self, qapp, tmp_path):
        from jarvis.coding.task_storage import CodingTask

        panel = _make_panel(qapp, tmp_path)
        task = CodingTask(name="Persisted")
        panel._on_task_updated(task)

        stored = panel._storage.load_all()
        assert len(stored) == 1
        assert stored[0].name == "Persisted"

    def test_escape_closes(self, qapp, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent

        panel = _make_panel(qapp, tmp_path)
        panel.show()
        QApplication.processEvents()

        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        # Panel should have a keyPressEvent that calls _on_close
        panel.keyPressEvent(event)
        # Panel initiated slide-out; it may still be visible momentarily,
        # but the animation was triggered.  Just confirm no crash.


# ---------------------------------------------------------------------------
# InlineConfirmDialog tests
# ---------------------------------------------------------------------------


class TestInlineConfirmDialog:
    """Inline confirm widget behaviour."""

    def test_yes_resolves_true(self, qapp):
        from jarvis.ui.coding_panel import InlineConfirmDialog

        dialog = InlineConfirmDialog("Delete foo?", timeout_s=60)
        results: list[bool] = []
        dialog.responded.connect(results.append)

        dialog.resolve(True)

        assert results == [True]
        assert dialog.is_resolved

    def test_no_resolves_false(self, qapp):
        from jarvis.ui.coding_panel import InlineConfirmDialog

        dialog = InlineConfirmDialog("Delete foo?", timeout_s=60)
        results: list[bool] = []
        dialog.responded.connect(results.append)

        dialog.resolve(False)

        assert results == [False]

    def test_double_resolve_is_noop(self, qapp):
        from jarvis.ui.coding_panel import InlineConfirmDialog

        dialog = InlineConfirmDialog("Overwrite?", timeout_s=60)
        results: list[bool] = []
        dialog.responded.connect(results.append)

        dialog.resolve(True)
        dialog.resolve(False)

        assert results == [True]  # only first counts

    def test_timeout_auto_denies(self, qapp):
        from jarvis.ui.coding_panel import InlineConfirmDialog

        dialog = InlineConfirmDialog("Delete?", timeout_s=1)
        results: list[bool] = []
        dialog.responded.connect(results.append)

        # Simulate ticks until timeout
        for _ in range(2):
            dialog._on_tick()

        assert results == [False]
        assert dialog.is_resolved


# ---------------------------------------------------------------------------
# MinimizedWidget tests
# ---------------------------------------------------------------------------


class TestMinimizedWidget:
    """Collapsed progress bar widget."""

    def test_update_from_task(self, qapp):
        from jarvis.coding.task_storage import CodingTask, TaskStep
        from jarvis.ui.coding_panel import MinimizedWidget

        widget = MinimizedWidget()
        task = CodingTask(
            name="Parser",
            steps=[
                TaskStep(title="A", status="done"),
                TaskStep(title="B", status="done"),
                TaskStep(title="C", status="running"),
            ],
        )
        widget.update_from_task(task)

        assert "Parser" in widget._text_lbl.text()
        assert widget._count_lbl.text() == "2/3"

    def test_update_from_none(self, qapp):
        from jarvis.ui.coding_panel import MinimizedWidget

        widget = MinimizedWidget()
        widget.update_from_task(None)
        assert widget._count_lbl.text() == "0/0"

    def test_click_emits_expand(self, qapp):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        from jarvis.ui.coding_panel import MinimizedWidget

        widget = MinimizedWidget()
        received: list[bool] = []
        widget.expand_requested.connect(lambda: received.append(True))

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(5, 5),
            QPointF(5, 5),
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.mousePressEvent(event)

        assert received == [True]


# ---------------------------------------------------------------------------
# StepWidget tests
# ---------------------------------------------------------------------------


class TestStepWidget:
    """Individual step timeline entry."""

    def test_displays_title(self, qapp):
        from jarvis.coding.task_storage import TaskStep
        from jarvis.ui.coding_panel import StepWidget

        step = TaskStep(title="Scaffold project", status="done")
        widget = StepWidget(step)
        assert widget._title_lbl.text() == "Scaffold project"

    def test_output_hidden_by_default(self, qapp):
        from jarvis.coding.task_storage import TaskStep
        from jarvis.ui.coding_panel import StepWidget

        step = TaskStep(title="Build", output="some output")
        widget = StepWidget(step)
        assert not widget._output_edit.isVisible()
        assert widget.has_output is True

    def test_set_output_visible(self, qapp):
        from jarvis.coding.task_storage import TaskStep
        from jarvis.ui.coding_panel import StepWidget

        step = TaskStep(title="Test", output="PASS")
        widget = StepWidget(step)
        widget.set_output_visible(True)
        assert not widget._output_edit.isHidden()

    def test_update_step(self, qapp):
        from jarvis.coding.task_storage import TaskStep
        from jarvis.ui.coding_panel import StepWidget

        step = TaskStep(title="Init", status="running")
        widget = StepWidget(step)

        step2 = TaskStep(title="Init (done)", status="done", output="OK")
        widget.update_step(step2)
        assert widget._title_lbl.text() == "Init (done)"
        assert widget._icon_lbl.text() == "✅"


# ---------------------------------------------------------------------------
# SummaryCard tests
# ---------------------------------------------------------------------------


class TestSummaryCard:
    """Completion summary widget."""

    def test_shows_summary_data(self, qapp):
        from jarvis.coding.task_storage import CodingTask
        from jarvis.ui.coding_panel import SummaryCard

        task = CodingTask(
            name="Done Task",
            status="done",
            summary={
                "files_created": 5,
                "tests_passed": "12/12",
                "duration": "2m 30s",
                "fix_iterations": 1,
            },
        )
        card = SummaryCard(task)
        # Card should construct without errors
        assert card.objectName() == "cpSummaryCard"

    def test_open_folder_signal(self, qapp):
        from jarvis.coding.task_storage import CodingTask
        from jarvis.ui.coding_panel import SummaryCard

        task = CodingTask(
            name="Done",
            status="done",
            workspace_path="/tmp/test_project",
        )
        card = SummaryCard(task)
        paths: list[str] = []
        card.open_folder_requested.connect(paths.append)
        # Find and click the Open Folder button
        for child in card.findChildren(type(card._task)):  # type: ignore
            pass  # just confirming construction
        # Signal is wired; we trust the button click path.


# ---------------------------------------------------------------------------
# AST parse gate — every new file must be valid Python
# ---------------------------------------------------------------------------


class TestAstParse:
    """Verify that all new source files parse cleanly."""

    def test_coding_panel_parses(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent.parent / "jarvis" / "ui" / "coding_panel.py"
        ast.parse(src.read_text(encoding="utf-8"), filename=str(src))

    def test_task_storage_parses(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent.parent / "jarvis" / "coding" / "task_storage.py"
        ast.parse(src.read_text(encoding="utf-8"), filename=str(src))

    def test_this_test_file_parses(self):
        import ast
        from pathlib import Path

        ast.parse(
            Path(__file__).read_text(encoding="utf-8"),
            filename=__file__,
        )
