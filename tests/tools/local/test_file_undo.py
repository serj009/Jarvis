"""Undo for deleted and overwritten files (M2-4, T12.2)."""

from __future__ import annotations

import re
import time

import pytest

from jarvis.core.request_context import current_user_transcription
from jarvis.tools.local import file_undo
from jarvis.tools.local.file_manager import FileManagerArgs, FileManagerTool
from jarvis.tools.local.file_undo import UndoFileChangeTool, UndoStore
from jarvis.tools.registry import EmptyArgs
from jarvis.tools.sandbox import PathValidator
from tests._typing import output_text


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def store(tmp_path):
    return UndoStore(tmp_path / "undo")


@pytest.fixture
def tools(work, store):
    validator = PathValidator(allowed_paths=[work], allow_home_fallback=False)
    return (
        FileManagerTool(path_validator=validator, undo_store=store),
        UndoFileChangeTool(store=store),
    )


async def _undo(undo_tool, said="верни как было"):
    token = current_user_transcription.set(said)
    try:
        return await undo_tool.execute(EmptyArgs())
    finally:
        current_user_transcription.reset(token)


async def test_deleted_file_comes_back(work, tools):
    fm, undo = tools
    report = work / "report.txt"
    report.write_text("Q3 numbers", encoding="utf-8")

    deleted = await fm.execute(FileManagerArgs(action="delete", path=str(report)))
    assert deleted.success
    assert not report.exists()
    assert "undo that" in output_text(deleted)

    result = await _undo(undo)
    assert result.success
    assert output_text(result) == "Вернул «report.txt» на место, сэр."
    assert report.read_text(encoding="utf-8") == "Q3 numbers"


async def test_deleted_folder_comes_back(work, tools):
    fm, undo = tools
    folder = work / "photos"
    (folder / "2026").mkdir(parents=True)
    (folder / "2026" / "a.jpg").write_bytes(b"jpg")

    assert (await fm.execute(FileManagerArgs(action="delete", path=str(folder)))).success
    assert not folder.exists()
    assert (await _undo(undo, "undo that")).success
    assert (folder / "2026" / "a.jpg").read_bytes() == b"jpg"


async def test_overwritten_file_is_kept_and_restored(work, tools):
    fm, undo = tools
    old = work / "notes.txt"
    old.write_text("old", encoding="utf-8")
    new = work / "draft.txt"
    new.write_text("new", encoding="utf-8")

    copied = await fm.execute(
        FileManagerArgs(action="copy", path=str(new), destination=str(old))
    )
    assert copied.success
    assert old.read_text(encoding="utf-8") == "new"

    result = await _undo(undo)
    assert output_text(result) == "Вернул прежнюю версию «notes.txt», сэр."
    assert old.read_text(encoding="utf-8") == "old"
    # The newer version is not lost either: one more undo brings it back.
    assert (await _undo(undo)).success
    assert old.read_text(encoding="utf-8") == "new"


async def test_move_into_a_folder_onto_a_same_named_file(work, tools):
    fm, undo = tools
    (work / "inbox").mkdir()
    (work / "inbox" / "a.txt").write_text("kept", encoding="utf-8")
    (work / "a.txt").write_text("moved", encoding="utf-8")
    moved = await fm.execute(
        FileManagerArgs(action="move", path=str(work / "a.txt"), destination=str(work / "inbox"))
    )
    assert moved.success
    assert (work / "inbox" / "a.txt").read_text(encoding="utf-8") == "moved"
    assert (await _undo(undo)).success
    assert (work / "inbox" / "a.txt").read_text(encoding="utf-8") == "kept"


async def test_nothing_to_undo(tools):
    _fm, undo = tools
    result = await _undo(undo, "undo that")
    assert result.success
    assert "nothing to undo" in output_text(result)


async def test_deleted_file_whose_place_was_taken_is_not_overwritten(work, tools):
    fm, undo = tools
    path = work / "a.txt"
    path.write_text("first", encoding="utf-8")
    await fm.execute(FileManagerArgs(action="delete", path=str(path)))
    path.write_text("second", encoding="utf-8")
    result = await _undo(undo)
    assert not result.success
    assert "уже новый файл" in (result.error or "")
    assert path.read_text(encoding="utf-8") == "second"


def test_store_keeps_at_most_max_entries(store, work, monkeypatch):
    monkeypatch.setattr(file_undo, "MAX_ENTRIES", 3)
    for i in range(5):
        f = work / f"f{i}.txt"
        f.write_text(str(i), encoding="utf-8")
        store.keep(f, "delete")
    names = [e.name for e in store.entries()]
    assert names == ["f2.txt", "f3.txt", "f4.txt"]
    assert len([p for p in store.root.iterdir() if p.is_dir()]) == 3


def test_store_forgets_entries_older_than_max_age(store, work, monkeypatch):
    old = work / "old.txt"
    old.write_text("x", encoding="utf-8")
    store.keep(old, "delete")
    later = time.time() + (file_undo.MAX_AGE_DAYS + 1) * 86400
    monkeypatch.setattr(file_undo.time, "time", lambda: later)
    fresh = work / "fresh.txt"
    fresh.write_text("y", encoding="utf-8")
    store.keep(fresh, "delete")
    assert [e.name for e in store.entries()] == ["fresh.txt"]


@pytest.mark.parametrize(
    "phrase",
    [
        "верни как было", "отмени удаление", "восстанови удалённый файл",
        "поверни як було", "віднови файл", "undo that", "restore the deleted file",
    ],
)
def test_undo_phrases_route_to_the_tool(phrase):
    from jarvis.tools.catalogue import voice_pattern_catalogue

    for tool_name, vp in voice_pattern_catalogue():
        if re.match(vp.regex, phrase, re.IGNORECASE):
            assert tool_name == "undo_file_change"
            return
    raise AssertionError(f"no pattern for {phrase!r}")
