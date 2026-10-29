"""Tests for jarvis.tools.local.disk_cleaner.

All disk access (psutil, os.walk, os.remove) is mocked — no real cleanup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.tools.local.disk_cleaner import (
    CleanTempFilesTool,
    DiskSpaceTool,
    FindLargeFoldersArgs,
    FindLargeFoldersTool,
)
from jarvis.tools.registry import EmptyArgs

# --- DiskSpaceTool --------------------------------------------------------

async def test_disk_space_success():
    mock_disks = [
        {
            "device": "C:\\",
            "mountpoint": "C:\\",
            "fstype": "NTFS",
            "total_gb": 500.0,
            "used_gb": 300.0,
            "free_gb": 200.0,
            "percent_used": 60.0,
        },
        {
            "device": "D:\\",
            "mountpoint": "D:\\",
            "fstype": "NTFS",
            "total_gb": 1000.0,
            "used_gb": 250.0,
            "free_gb": 750.0,
            "percent_used": 25.0,
        },
    ]
    with patch(
        "jarvis.tools.local.disk_cleaner._disk_space_impl",
        return_value=mock_disks,
    ):
        with patch(
            "jarvis.tools.local.disk_cleaner._get_temp_info",
            return_value={"total_size_mb": 512.0, "total_files": 1234, "directories": []},
        ):
            result = await DiskSpaceTool().execute(EmptyArgs())
    assert result.success
    assert "C:\\" in (result.output or "")
    assert "D:\\" in (result.output or "")
    assert "60.0%" in (result.output or "")
    assert "512" in (result.output or "")


async def test_disk_space_not_implemented():
    with patch(
        "jarvis.tools.local.disk_cleaner._disk_space_impl",
        side_effect=NotImplementedError("psutil required"),
    ):
        result = await DiskSpaceTool().execute(EmptyArgs())
    assert not result.success


async def test_disk_space_empty():
    with patch(
        "jarvis.tools.local.disk_cleaner._disk_space_impl",
        return_value=[],
    ):
        result = await DiskSpaceTool().execute(EmptyArgs())
    assert result.success
    assert "no disk" in (result.output or "").lower()


# --- FindLargeFoldersTool -------------------------------------------------

async def test_find_large_folders_success():
    mock_folders = [
        {"name": "node_modules", "path": r"C:\project\node_modules", "size_mb": 512.0, "file_count": 50000},
        {"name": ".git", "path": r"C:\project\.git", "size_mb": 128.0, "file_count": 5000},
    ]
    with patch(
        "jarvis.tools.local.disk_cleaner._find_large_folders_impl",
        return_value=mock_folders,
    ):
        result = await FindLargeFoldersTool().execute(
            FindLargeFoldersArgs(path=r"C:\project"),
        )
    assert result.success
    assert "node_modules" in (result.output or "")
    assert "512" in (result.output or "")


async def test_find_large_folders_not_found():
    with patch(
        "jarvis.tools.local.disk_cleaner._find_large_folders_impl",
        side_effect=FileNotFoundError("Directory not found: /nonexistent"),
    ):
        result = await FindLargeFoldersTool().execute(
            FindLargeFoldersArgs(path="/nonexistent"),
        )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_find_large_folders_empty():
    with patch(
        "jarvis.tools.local.disk_cleaner._find_large_folders_impl",
        return_value=[],
    ):
        result = await FindLargeFoldersTool().execute(
            FindLargeFoldersArgs(path=r"C:\empty"),
        )
    assert result.success
    assert "no subfolder" in (result.output or "").lower()


# --- CleanTempFilesTool ---------------------------------------------------

async def test_clean_temp_success():
    mock_result = {"cleaned_size_mb": 256.0, "cleaned_files": 500, "errors": 3}
    with patch(
        "jarvis.tools.local.disk_cleaner._clean_temp_impl",
        return_value=mock_result,
    ):
        result = await CleanTempFilesTool().execute(EmptyArgs())
    assert result.success
    assert "500" in (result.output or "")
    assert "256" in (result.output or "")
    assert "3 files could not" in (result.output or "")


async def test_clean_temp_no_errors():
    mock_result = {"cleaned_size_mb": 100.0, "cleaned_files": 200, "errors": 0}
    with patch(
        "jarvis.tools.local.disk_cleaner._clean_temp_impl",
        return_value=mock_result,
    ):
        result = await CleanTempFilesTool().execute(EmptyArgs())
    assert result.success
    assert "could not" not in (result.output or "")


async def test_clean_temp_failure():
    with patch(
        "jarvis.tools.local.disk_cleaner._clean_temp_impl",
        side_effect=PermissionError("Access denied"),
    ):
        result = await CleanTempFilesTool().execute(EmptyArgs())
    assert not result.success


def test_requires_confirmation_false():
    """Confirmation is gated via TOOL_RISK_MAP, not requires_confirmation."""
    assert DiskSpaceTool().requires_confirmation is False
    assert FindLargeFoldersTool().requires_confirmation is False
    assert CleanTempFilesTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(DiskSpaceTool.voice_patterns) >= 4
    assert len(FindLargeFoldersTool.voice_patterns) >= 3
    assert len(CleanTempFilesTool.voice_patterns) >= 3


def test_find_large_folders_args_bounds():
    FindLargeFoldersArgs(path="C:\\", top_n=1)  # OK
    FindLargeFoldersArgs(path="C:\\", top_n=50)  # OK
    with pytest.raises(Exception):
        FindLargeFoldersArgs(path="C:\\", top_n=0)
    with pytest.raises(Exception):
        FindLargeFoldersArgs(path="C:\\", top_n=51)
