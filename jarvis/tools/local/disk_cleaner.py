"""Disk cleaner — free space, large folders, temp file cleanup.

Uses psutil for disk usage and os/shutil for file operations.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UAC / elevation helpers
# ---------------------------------------------------------------------------

_UAC_MSG = (
    "I can't do that without administrator privileges, sir. "
    "Try running JARVIS as admin, or do it manually."
)


def _is_access_denied(exc: OSError) -> bool:
    """Return True if *exc* is a Windows ERROR_ACCESS_DENIED (winerror 5)."""
    return getattr(exc, "winerror", None) == 5



# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_psutil():
    """Lazy import psutil."""
    try:
        import psutil
        return psutil
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _disk_space_impl() -> list[dict]:
    """Get free space on all disk partitions."""
    psutil = _get_psutil()
    if psutil is None:
        raise NotImplementedError(
            "Disk space info requires psutil. Install: pip install psutil"
        )

    disks: list[dict] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "percent_used": usage.percent,
            })
        except (PermissionError, OSError):
            continue

    return disks


def _find_large_folders_impl(path: str, top_n: int = 10) -> list[dict]:
    """Find the largest folders in a directory (1 level deep)."""
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Directory not found: {path}")

    folder_sizes: list[dict] = []
    try:
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                try:
                    total_size = 0
                    file_count = 0
                    for dirpath, _dirnames, filenames in os.walk(entry.path):
                        for f in filenames:
                            try:
                                fp = os.path.join(dirpath, f)
                                total_size += os.path.getsize(fp)
                                file_count += 1
                            except (OSError, PermissionError):
                                continue
                    folder_sizes.append({
                        "name": entry.name,
                        "path": entry.path,
                        "size_mb": round(total_size / (1024 ** 2), 2),
                        "file_count": file_count,
                    })
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        raise PermissionError(f"Cannot access directory: {path}")

    folder_sizes.sort(key=lambda x: x["size_mb"], reverse=True)
    return folder_sizes[:top_n]


def _get_temp_info() -> dict:
    """Get info about temp files."""
    temp_dirs = [
        tempfile.gettempdir(),  # %TEMP%
    ]
    # Add Windows\Temp if it exists
    win_temp = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")
    if os.path.isdir(win_temp) and win_temp not in temp_dirs:
        temp_dirs.append(win_temp)

    total_size = 0
    total_files = 0
    details: list[dict] = []

    for temp_dir in temp_dirs:
        dir_size = 0
        dir_files = 0
        try:
            for dirpath, _dirnames, filenames in os.walk(temp_dir):
                for f in filenames:
                    try:
                        fp = os.path.join(dirpath, f)
                        dir_size += os.path.getsize(fp)
                        dir_files += 1
                    except (OSError, PermissionError):
                        continue
        except (PermissionError, OSError):
            continue

        total_size += dir_size
        total_files += dir_files
        details.append({
            "path": temp_dir,
            "size_mb": round(dir_size / (1024 ** 2), 2),
            "file_count": dir_files,
        })

    return {
        "total_size_mb": round(total_size / (1024 ** 2), 2),
        "total_files": total_files,
        "directories": details,
    }


def _clean_temp_impl() -> dict:
    """Clean temp files. Returns stats on what was cleaned."""
    temp_dir = tempfile.gettempdir()
    cleaned_size = 0
    cleaned_files = 0
    errors = 0

    for dirpath, dirnames, filenames in os.walk(temp_dir, topdown=False):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                cleaned_size += size
                cleaned_files += 1
            except (OSError, PermissionError):
                errors += 1
                continue

        for d in dirnames:
            dp = os.path.join(dirpath, d)
            try:
                os.rmdir(dp)  # only removes empty dirs
            except (OSError, PermissionError):
                continue

    return {
        "cleaned_size_mb": round(cleaned_size / (1024 ** 2), 2),
        "cleaned_files": cleaned_files,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class FindLargeFoldersArgs(BaseModel):
    path: str = Field(
        description="Directory path to scan for large subfolders.",
    )
    top_n: int = Field(
        default=10, ge=1, le=50,
        description="Number of largest folders to return (1-50).",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class DiskSpaceTool:
    """Show free space on all disks."""

    name: str = "disk_space"
    description: str = (
        "Show disk space usage on all drives (total, used, free). "
        "Also shows temp file sizes. "
        "(RU: место на диске, свободное место / UK: місце на диску, вільне місце)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:disk|drive)\s+space$",
            priority=200,
        ),
        VoicePattern(
            regex=r"^free\s+space$",
            priority=200,
        ),
        # RU
        VoicePattern(
            regex=r"^(?:место|свободное\s+место)\s+на\s+диск[еу]$",
            priority=200,
        ),
        # UK
        VoicePattern(
            regex=r"^(?:місце|вільне\s+місце)\s+на\s+диск[уі]$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            disks = await asyncio.to_thread(_disk_space_impl)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("disk_space failed")
            return ToolResult(success=False, error=f"Failed to get disk space: {e}")
        except Exception as e:
            log.exception("disk_space failed")
            return ToolResult(success=False, error=f"Failed to get disk space: {e}")

        if not disks:
            return ToolResult(success=True, output="No disk information available, sir.")

        lines = ["Disk space, sir:"]
        for d in disks:
            bar_filled = int(d["percent_used"] / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            lines.append(
                f"  • {d['device']} ({d['mountpoint']}) [{d['fstype']}]\n"
                f"    [{bar}] {d['percent_used']}%\n"
                f"    {d['used_gb']} GB used / {d['total_gb']} GB total / "
                f"{d['free_gb']} GB free"
            )

        # Temp info
        try:
            temp = await asyncio.to_thread(_get_temp_info)
            lines.append(
                f"\n  Temp files: {temp['total_size_mb']} MB "
                f"in {temp['total_files']} files"
            )
        except Exception:
            pass

        return ToolResult(success=True, output="\n".join(lines))


class FindLargeFoldersTool:
    """Find the largest subfolders in a directory."""

    name: str = "find_large_folders"
    description: str = (
        "Find the biggest subfolders in a given directory. "
        "Useful for identifying space hogs. "
        "(RU: самые большие папки / UK: найбільші папки)"
    )
    args_schema = FindLargeFoldersArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:biggest|largest)\s+folders(?:\s+in\s+(.+))?$",
            priority=200,
            args=lambda m: {"path": m.group(1) or "C:\\"},
        ),
        # RU
        VoicePattern(
            regex=r"^самые\s+большие\s+папки(?:\s+в\s+(.+))?$",
            priority=200,
            args=lambda m: {"path": m.group(1) or "C:\\"},
        ),
        # UK
        VoicePattern(
            regex=r"^найбільші\s+папки(?:\s+в\s+(.+))?$",
            priority=200,
            args=lambda m: {"path": m.group(1) or "C:\\"},
        ),
    )

    async def execute(self, args: FindLargeFoldersArgs) -> ToolResult:
        try:
            folders = await asyncio.to_thread(
                _find_large_folders_impl, args.path, args.top_n,
            )
        except FileNotFoundError as e:
            return ToolResult(success=False, error=str(e))
        except PermissionError as e:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("find_large_folders failed")
            return ToolResult(success=False, error=f"Failed to scan: {e}")
        except Exception as e:
            log.exception("find_large_folders failed")
            return ToolResult(success=False, error=f"Failed to scan: {e}")

        if not folders:
            return ToolResult(
                success=True,
                output=f"No subfolders found in {args.path}, sir.",
            )

        lines = [f"Top {len(folders)} folders in {args.path}, sir:"]
        for i, f in enumerate(folders, 1):
            lines.append(
                f"  {i}. {f['name']} — {f['size_mb']} MB ({f['file_count']} files)"
            )

        return ToolResult(success=True, output="\n".join(lines))


class CleanTempFilesTool:
    """Clean temporary files."""

    name: str = "clean_temp_files"
    description: str = (
        "Delete temporary files from the system temp directory. "
        "Requires confirmation (destructive). "
        "(RU: почисти временные файлы / UK: почисти тимчасові файли)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False  # Gated via TOOL_RISK_MAP = DESTRUCTIVE
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^clean\s+temp(?:orary)?\s+files$",
            priority=200,
        ),
        # RU
        VoicePattern(
            regex=r"^почисти\s+временные\s+файлы$",
            priority=200,
        ),
        # UK
        VoicePattern(
            regex=r"^почисти\s+тимчасові\s+файли$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(_clean_temp_impl)
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("clean_temp_files failed")
            return ToolResult(success=False, error=f"Temp cleanup failed: {e}")
        except Exception as e:
            log.exception("clean_temp_files failed")
            return ToolResult(success=False, error=f"Temp cleanup failed: {e}")

        return ToolResult(
            success=True,
            output=(
                f"Cleaned {result['cleaned_files']} temp files "
                f"({result['cleaned_size_mb']} MB freed), sir."
                + (f" {result['errors']} files could not be deleted (in use)."
                   if result['errors'] else "")
            ),
        )
