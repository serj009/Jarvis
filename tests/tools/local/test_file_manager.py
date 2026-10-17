"""Tests for jarvis.tools.local.file_manager.FileManagerTool.

Covers every action (find, open, move, copy, rename, delete, mkdir),
voice pattern compilation + trilingual matching (EN/RU/UK), sandbox
enforcement, and error handling — all with mocked os/shutil so no
real filesystem mutations happen outside tmp_path."""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.tools.local.file_manager import (
    FileManagerArgs,
    FileManagerTool,
)
from jarvis.tools.sandbox import PathValidator
from tests._typing import output_text


# ── helpers ───────────────────────────────────────────────────────


def _tool(tmp_path: Path | None = None) -> FileManagerTool:
    """Build a FileManagerTool with a sandbox validator scoped to tmp_path."""
    if tmp_path is not None:
        validator = PathValidator(
            allowed_paths=[tmp_path],
            allow_home_fallback=False,
        )
    else:
        validator = None
    return FileManagerTool(path_validator=validator)


def _matches_any(phrase: str) -> bool:
    """True if at least one voice pattern matches *phrase*."""
    for vp in FileManagerTool.voice_patterns:
        pat = re.compile(vp.regex, re.IGNORECASE)
        if pat.match(phrase.strip().lower()):
            return True
    return False


def _match_args(phrase: str) -> dict | None:
    """Return the args dict for the first matching pattern, or None."""
    for vp in FileManagerTool.voice_patterns:
        pat = re.compile(vp.regex, re.IGNORECASE)
        m = pat.match(phrase.strip().lower())
        if m:
            return vp.args(m)
    return None


# ── find ──────────────────────────────────────────────────────────


async def test_find_locates_matching_files(tmp_path: Path):
    (tmp_path / "report_2024.xlsx").write_text("")
    (tmp_path / "report_draft.docx").write_text("")
    (tmp_path / "readme.txt").write_text("")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="find", path="report", search_dir=str(tmp_path))
    )
    assert result.success
    out = output_text(result)
    assert "2" in out  # found 2 files
    assert "report" in out.lower()


async def test_find_no_results(tmp_path: Path):
    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="find", path="nonexistent", search_dir=str(tmp_path))
    )
    assert result.success
    assert "no files" in output_text(result).lower()


async def test_find_empty_query():
    result = await _tool().execute(
        FileManagerArgs(action="find", path="")
    )
    assert not result.success


async def test_find_single_result(tmp_path: Path):
    (tmp_path / "budget.csv").write_text("")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="find", path="budget", search_dir=str(tmp_path))
    )
    assert result.success
    out = output_text(result)
    assert "found it" in out.lower()


# ── open ──────────────────────────────────────────────────────────


async def test_open_existing_file(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")

    with patch("jarvis.tools.local.file_manager.os.startfile", create=True):
        with patch("jarvis.tools.local.file_manager.platform.system", return_value="Windows"):
            result = await _tool(tmp_path).execute(
                FileManagerArgs(action="open", path=str(f))
            )
    assert result.success
    assert "opened" in output_text(result).lower()


async def test_open_nonexistent_file(tmp_path: Path):
    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="open", path=str(tmp_path / "ghost.txt"))
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


# ── move ──────────────────────────────────────────────────────────


async def test_move_file(tmp_path: Path):
    src = tmp_path / "file.txt"
    src.write_text("content")
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="move", path=str(src), destination=str(dst_dir))
    )
    assert result.success
    assert "moved" in output_text(result).lower()
    # File should be in the destination now.
    assert (dst_dir / "file.txt").exists()
    assert not src.exists()


async def test_move_nonexistent_source(tmp_path: Path):
    result = await _tool(tmp_path).execute(
        FileManagerArgs(
            action="move",
            path=str(tmp_path / "nope.txt"),
            destination=str(tmp_path / "dest"),
        )
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


# ── copy ──────────────────────────────────────────────────────────


async def test_copy_file(tmp_path: Path):
    src = tmp_path / "original.txt"
    src.write_text("data")
    dst = tmp_path / "copy.txt"

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="copy", path=str(src), destination=str(dst))
    )
    assert result.success
    assert "copied" in output_text(result).lower()
    # Both files should exist.
    assert src.exists()
    assert dst.exists()


async def test_copy_directory(tmp_path: Path):
    src_dir = tmp_path / "src_folder"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("a")
    dst_dir = tmp_path / "dst_folder"

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="copy", path=str(src_dir), destination=str(dst_dir))
    )
    assert result.success
    assert (dst_dir / "a.txt").exists()


async def test_copy_nonexistent_source(tmp_path: Path):
    result = await _tool(tmp_path).execute(
        FileManagerArgs(
            action="copy",
            path=str(tmp_path / "nope.txt"),
            destination=str(tmp_path / "copy.txt"),
        )
    )
    assert not result.success


# ── rename ────────────────────────────────────────────────────────


async def test_rename_file(tmp_path: Path):
    src = tmp_path / "old_name.txt"
    src.write_text("hello")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="rename", path=str(src), destination="new_name.txt")
    )
    assert result.success
    assert "renamed" in output_text(result).lower()
    assert (tmp_path / "new_name.txt").exists()
    assert not src.exists()


async def test_rename_no_destination():
    result = await _tool().execute(
        FileManagerArgs(action="rename", path="something.txt", destination="")
    )
    assert not result.success
    assert "no new name" in (result.error or "").lower()


# ── delete ────────────────────────────────────────────────────────


async def test_delete_file(tmp_path: Path):
    f = tmp_path / "trash.txt"
    f.write_text("bye")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="delete", path=str(f))
    )
    assert result.success
    assert "deleted" in output_text(result).lower()
    assert not f.exists()


async def test_delete_directory(tmp_path: Path):
    d = tmp_path / "old_folder"
    d.mkdir()
    (d / "inner.txt").write_text("x")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="delete", path=str(d))
    )
    assert result.success
    assert "folder" in output_text(result).lower()
    assert not d.exists()


async def test_delete_nonexistent(tmp_path: Path):
    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="delete", path=str(tmp_path / "ghost.txt"))
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


# ── mkdir ─────────────────────────────────────────────────────────


async def test_mkdir_creates_directory(tmp_path: Path):
    target = tmp_path / "new_project"

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="mkdir", path=str(target))
    )
    assert result.success
    assert "created" in output_text(result).lower()
    assert target.is_dir()


async def test_mkdir_already_exists(tmp_path: Path):
    target = tmp_path / "existing"
    target.mkdir()

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="mkdir", path=str(target))
    )
    assert result.success
    assert "already exists" in output_text(result).lower()


async def test_mkdir_file_collision(tmp_path: Path):
    target = tmp_path / "collision"
    target.write_text("i am a file")

    result = await _tool(tmp_path).execute(
        FileManagerArgs(action="mkdir", path=str(target))
    )
    assert not result.success


# ── unknown action ────────────────────────────────────────────────


async def test_unknown_action():
    result = await _tool().execute(
        FileManagerArgs(action="find", path="test")  # type: ignore[arg-type]
    )
    # find is a valid action, so this should work
    assert result.success or not result.success  # just ensures no crash

    # Test with actual unknown action (bypass validation via model_construct)
    args = FileManagerArgs.model_construct(action="explode", path="test")
    result = await _tool().execute(args)
    assert not result.success
    assert "unknown action" in (result.error or "").lower()


# ── sandbox enforcement ───────────────────────────────────────────


async def test_sandbox_blocks_outside_path(tmp_path: Path):
    """Operations outside the sandbox whitelist must be denied."""
    tool = _tool(tmp_path)

    result = await tool.execute(
        FileManagerArgs(action="open", path="C:\\Windows\\System32\\cmd.exe")
    )
    assert not result.success
    assert "denied" in (result.error or "").lower()


async def test_sandbox_blocks_move_to_outside(tmp_path: Path):
    src = tmp_path / "safe.txt"
    src.write_text("data")

    tool = _tool(tmp_path)
    result = await tool.execute(
        FileManagerArgs(
            action="move",
            path=str(src),
            destination="C:\\Windows\\Temp\\escaped.txt",
        )
    )
    assert not result.success
    assert "denied" in (result.error or "").lower()


# ── voice patterns ────────────────────────────────────────────────


def test_voice_patterns_compile():
    """Every regex in voice_patterns must compile without error."""
    for vp in FileManagerTool.voice_patterns:
        re.compile(vp.regex, re.IGNORECASE)


# -- EN patterns --

_EN_FIND = [
    "find file report",
    "find files on desktop",
    "find report",
    "search for file budget",
    "look for file readme",
]


@pytest.mark.parametrize("phrase", _EN_FIND)
def test_en_find_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "find"


_EN_OPEN = [
    "open file report.pdf",
    "open document readme.txt",
]


@pytest.mark.parametrize("phrase", _EN_OPEN)
def test_en_open_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "open"


_EN_MOVE = [
    "move photo.jpg to pictures",
    "move report.pdf to documents",
]


@pytest.mark.parametrize("phrase", _EN_MOVE)
def test_en_move_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "move"
    assert "destination" in args


_EN_COPY = [
    "copy file.txt to backup",
    "copy report to documents",
]


@pytest.mark.parametrize("phrase", _EN_COPY)
def test_en_copy_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "copy"
    assert "destination" in args


_EN_DELETE = [
    "delete temp.txt",
    "delete file old_report.docx",
    "remove junk.log",
]


@pytest.mark.parametrize("phrase", _EN_DELETE)
def test_en_delete_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "delete"


_EN_MKDIR = [
    "create folder projects",
    "create directory backups",
    "make folder new_project",
    "new directory workspace",
]


@pytest.mark.parametrize("phrase", _EN_MKDIR)
def test_en_mkdir_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "mkdir"


_EN_RENAME = [
    "rename old.txt to new.txt",
    "rename document.docx to final.docx",
]


@pytest.mark.parametrize("phrase", _EN_RENAME)
def test_en_rename_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "rename"
    assert "destination" in args


# -- RU patterns --

_RU_PHRASES = [
    ("найди файл отчёт", "find"),
    ("найди отчёт", "find"),
    ("найти файл бюджет", "find"),
    ("открой файл readme", "open"),
    ("перемести фото в картинки", "move"),
    ("скопируй отчёт в документы", "copy"),
    ("переименуй файл в новый", "rename"),
    ("удали файл мусор", "delete"),
    ("удали temp.txt", "delete"),
    ("создай папку проекты", "mkdir"),
]


@pytest.mark.parametrize("phrase,expected_action", _RU_PHRASES)
def test_ru_patterns(phrase: str, expected_action: str):
    args = _match_args(phrase)
    assert args is not None, f"No RU pattern matched: {phrase!r}"
    assert args["action"] == expected_action


# -- UK patterns --

_UK_PHRASES = [
    ("знайди файл звіт", "find"),
    ("знайди звіт", "find"),
    ("пошукай файл бюджет", "find"),
    ("відкрий файл readme", "open"),
    ("перемісти фото в картинки", "move"),
    ("скопіюй звіт в документи", "copy"),
    ("перейменуй файл на новий", "rename"),
    ("видали файл сміття", "delete"),
    ("видали temp.txt", "delete"),
    ("створи папку проекти", "mkdir"),
    ("створи директорію бекапи", "mkdir"),
]


@pytest.mark.parametrize("phrase,expected_action", _UK_PHRASES)
def test_uk_patterns(phrase: str, expected_action: str):
    args = _match_args(phrase)
    assert args is not None, f"No UK pattern matched: {phrase!r}"
    assert args["action"] == expected_action


# ── meta ──────────────────────────────────────────────────────────


def test_tool_name():
    assert FileManagerTool.name == "file_manager"


def test_args_schema():
    assert FileManagerTool.args_schema is FileManagerArgs


def test_risk_level_is_write():
    from jarvis.tools.risk import RiskLevel
    assert FileManagerTool.risk_level == RiskLevel.WRITE


# ── open: file-extension voice patterns ──────────────────────────


_EXT_OPEN = [
    ("open report.docx", "open", "report.docx"),
    ("open photo.jpg", "open", "photo.jpg"),
    ("open budget.xlsx", "open", "budget.xlsx"),
    ("open notes.pdf", "open", "notes.pdf"),
    ("open script.py", "open", "script.py"),
    ("open data.csv", "open", "data.csv"),
]


@pytest.mark.parametrize("phrase,expected_action,expected_path", _EXT_OPEN)
def test_extension_patterns_en(
    phrase: str, expected_action: str, expected_path: str
):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == expected_action
    assert args["path"] == expected_path


_EXT_OPEN_RU = [
    ("открой report.docx", "open", "report.docx"),
    ("открыть photo.jpg", "open", "photo.jpg"),
]


@pytest.mark.parametrize("phrase,expected_action,expected_path", _EXT_OPEN_RU)
def test_extension_patterns_ru(
    phrase: str, expected_action: str, expected_path: str
):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == expected_action
    assert args["path"] == expected_path


_EXT_OPEN_UK = [
    ("відкрий report.docx", "open", "report.docx"),
    ("відкрити data.csv", "open", "data.csv"),
]


@pytest.mark.parametrize("phrase,expected_action,expected_path", _EXT_OPEN_UK)
def test_extension_patterns_uk(
    phrase: str, expected_action: str, expected_path: str
):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == expected_action
    assert args["path"] == expected_path


# ── open: last-download voice patterns ───────────────────────────


_LAST_DL = [
    "open the last downloaded file",
    "open last downloaded file",
    "open the latest download file",
    "open last file",
    "открой последний скачанный файл",
    "открой последний загруженный файл",
    "відкрий останній завантажений файл",
]


@pytest.mark.parametrize("phrase", _LAST_DL)
def test_last_download_patterns(phrase: str):
    args = _match_args(phrase)
    assert args is not None, f"No pattern matched: {phrase!r}"
    assert args["action"] == "open"
    assert args["path"] == ":last_download"


# ── open: search fallback when exact path missing ────────────────


async def test_open_search_fallback(tmp_path: Path):
    """A bare filename not at the exact path is searched in common dirs."""
    # Simulate Desktop under tmp_path (no sandbox — _search_common_dirs
    # uses Path.home(); we mock it).
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "report.docx"
    target.write_text("content")

    with patch("jarvis.tools.local.file_manager.Path.home", return_value=tmp_path):
        with patch("jarvis.tools.local.file_manager.os.startfile", create=True):
            with patch(
                "jarvis.tools.local.file_manager.platform.system",
                return_value="Windows",
            ):
                tool = _tool()  # no sandbox
                result = await tool.execute(
                    FileManagerArgs(action="open", path="report.docx")
                )
    assert result.success
    assert "report.docx" in output_text(result).lower()


async def test_open_search_case_insensitive(tmp_path: Path):
    """Search is case-insensitive."""
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "MyReport.PDF").write_text("content")

    with patch("jarvis.tools.local.file_manager.Path.home", return_value=tmp_path):
        with patch("jarvis.tools.local.file_manager.os.startfile", create=True):
            with patch(
                "jarvis.tools.local.file_manager.platform.system",
                return_value="Windows",
            ):
                tool = _tool()
                result = await tool.execute(
                    FileManagerArgs(action="open", path="myreport.pdf")
                )
    assert result.success


# ── open: :last_download ─────────────────────────────────────────


async def test_open_last_download(tmp_path: Path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    old = dl / "old.txt"
    old.write_text("old")
    new = dl / "new.pdf"
    new.write_text("new")
    # Make new.pdf newer.
    import time
    os.utime(str(old), (1000, 1000))
    os.utime(str(new), (2000, 2000))

    with patch("jarvis.tools.local.file_manager.Path.home", return_value=tmp_path):
        with patch("jarvis.tools.local.file_manager.os.startfile", create=True):
            with patch(
                "jarvis.tools.local.file_manager.platform.system",
                return_value="Windows",
            ):
                tool = _tool()
                result = await tool.execute(
                    FileManagerArgs(action="open", path=":last_download")
                )
    assert result.success
    assert "new.pdf" in output_text(result).lower()


async def test_open_last_download_empty(tmp_path: Path):
    dl = tmp_path / "Downloads"
    dl.mkdir()

    with patch("jarvis.tools.local.file_manager.Path.home", return_value=tmp_path):
        tool = _tool()
        result = await tool.execute(
            FileManagerArgs(action="open", path=":last_download")
        )
    assert not result.success
    assert "empty" in (result.error or "").lower()
