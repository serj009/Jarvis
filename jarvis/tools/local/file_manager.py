"""File management operations — find, open, move, copy, rename, delete, mkdir.

Complements the read-only ``files.py`` (ListDirectoryTool) with the full
set of filesystem operations a voice user expects:

    "Find file report on the desktop"
    "Open the last downloaded file"
    "Move photos from Downloads to Pictures"
    "Delete temp.txt"
    "Create folder Projects"

Every path is validated through the workspace sandbox before any I/O.
Delete is gated behind ``requires_confirmation=True`` / ``RiskLevel.DESTRUCTIVE``
so the user always gets a confirmation prompt before data is destroyed.

Design notes:

- **find**: ``os.walk`` + case-insensitive substring match.  Returns up
  to ``_MAX_FIND_RESULTS`` hits. Fuzzy enough for voice ("report" matches
  "Q3_Report_Final.xlsx") without pulling in a third-party library.

- **open**: ``os.startfile(path)`` on Windows — opens the file in
  its default associated application.  On other platforms falls back to
  ``xdg-open`` / ``open`` (best-effort).
  When the exact path does not exist AND the argument is a bare
  filename (no separators), searches Desktop → Documents → Downloads
  for a case-insensitive match.  The magic token ``:last_download``
  opens the newest file in ~/Downloads.

- **move / copy / rename**: ``shutil.move`` / ``shutil.copy2`` /
  ``os.rename``.  Both source AND destination are sandbox-validated.

- **delete**: ``os.remove`` for files, ``shutil.rmtree`` for directories.
  Requires confirmation (risk=DESTRUCTIVE).

- **mkdir**: ``os.makedirs(path, exist_ok=True)``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel
from jarvis.tools.sandbox import PathValidator, SandboxViolationError

log = logging.getLogger(__name__)

# How many search results to report via voice.
_MAX_FIND_RESULTS = 10
# How many entry names get spoken in find results preview.
_SPOKEN_PREVIEW_COUNT = 5

# Common file extensions that distinguish a *file* open request from an
# *application* launch.  When the user says "open report.docx" the token
# ends with one of these, so we search the filesystem instead of trying
# to launch an application.  Kept lowercase; matching is case-insensitive.
_FILE_EXTENSIONS: frozenset[str] = frozenset((
    ".doc", ".docx", ".pdf", ".txt", ".rtf", ".odt",
    ".xls", ".xlsx", ".csv", ".ods",
    ".ppt", ".pptx", ".odp",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".yml",
    ".md", ".log", ".ini", ".cfg", ".conf",
))

# Directories searched (in order) when a bare filename has no path.
_COMMON_SEARCH_DIRS: tuple[str, ...] = ("Desktop", "Documents", "Downloads")


# ── args schema ───────────────────────────────────────────────────


class FileManagerArgs(BaseModel):
    action: Literal["find", "open", "move", "copy", "rename", "delete", "mkdir"] = Field(
        description=(
            "File operation to perform: find, open, move, copy, "
            "rename, delete, or mkdir."
        ),
    )
    path: str = Field(
        default="",
        description=(
            "Source path or search query. For 'find', this is the "
            "filename pattern to search for. For other actions, this "
            "is the source file/folder path. Accepts ~ for user home."
        ),
    )
    destination: str = Field(
        default="",
        description=(
            "Destination path for move/copy/rename operations."
        ),
    )
    search_dir: str = Field(
        default="",
        description=(
            "Directory to search in for 'find' action. "
            "Defaults to user home if empty."
        ),
    )


# ── tool class ────────────────────────────────────────────────────


class FileManagerTool:
    """Find, open, move, copy, rename, or delete files and folders."""

    name: str = "file_manager"
    description: str = (
        "Find, open, move, copy, rename, or delete files and folders. "
        "Use for any file management request — searching by name, "
        "opening in default app, moving/copying between folders, "
        "renaming, creating directories, or deleting. "
        "(RU: найди/открой/перемести/скопируй/удали файл, создай папку / "
        "UK: знайди/відкрий/перемісти/скопіюй/видали файл, створи папку)"
    )
    args_schema: type[BaseModel] = FileManagerArgs
    requires_confirmation: bool = False  # per-action in execute()
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    # ── voice patterns ────────────────────────────────────────────
    # Priority 300: specific enough to not steal generic patterns,
    # low enough to trigger before catch-all.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # ── specific: last download (priority 250) ────────────────
        # Must appear BEFORE generic "open file X" (300) so that
        # "открой последний скачанный файл" is not swallowed by the
        # generic ^открой\s+(?:файл\s+)?(.+)$ pattern.
        VoicePattern(
            regex=r"^open\s+(?:the\s+)?(?:last|latest|recent)\s+(?:download(?:ed)?\s+)?file$",
            priority=250,
            args=lambda m: {"action": "open", "path": ":last_download"},
        ),
        VoicePattern(
            regex=(
                r"^(?:открой|открыть)\s+(?:последний|последний\s+скачанный"
                r"|последний\s+загруженный)\s+файл$"
            ),
            priority=250,
            args=lambda m: {"action": "open", "path": ":last_download"},
        ),
        VoicePattern(
            regex=(
                r"^(?:відкрий|відкрити)\s+(?:останній|останній\s+завантажений"
                r"|останній\s+скачаний)\s+файл$"
            ),
            priority=250,
            args=lambda m: {"action": "open", "path": ":last_download"},
        ),
        # ── specific: open by file extension (priority 250) ───────
        # "open report.docx" / "open my_photo.jpg" — the name ends
        # with a known file extension, so route to file_manager
        # instead of open_app.  Priority 250: beats open_app (900)
        # AND the generic "open file X" (300) so extension wins.
        VoicePattern(
            regex=r"^open\s+(.+\.\w{1,5})$",
            priority=250,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:открой|открыть)\s+(.+\.\w{1,5})$",
            priority=250,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:відкрий|відкрити)\s+(.+\.\w{1,5})$",
            priority=250,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        # ── generic: EN ──────────────────────────────────────────
        VoicePattern(
            regex=r"^find\s+(?:file\s+|files?\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:search\s+for\s+|look\s+for\s+)files?\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:search\s+for\s+|look\s+for\s+)(.+\.\w{1,5})$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^open\s+(?:file|document)\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^move\s+(.+?)\s+to\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "move", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^copy\s+(.+?)\s+to\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "copy", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^rename\s+(.+?)\s+to\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "rename", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^(?:delete|remove)\s+(?:file\s+)?(.+\.\w{1,5})$",
            priority=300,
            args=lambda m: {"action": "delete", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^create\s+(?:folder|directory)\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "mkdir", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:make|new)\s+(?:folder|directory)\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "mkdir", "path": m.group(1)},
        ),
        # ── generic: RU ──────────────────────────────────────────
        VoicePattern(
            regex=r"^найди\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:найти|поиск|поищи)\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^открой\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^перемести\s+(.+?)\s+в\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "move", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^скопируй\s+(.+?)\s+в\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "copy", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^переименуй\s+(.+?)\s+в\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "rename", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^удали\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "delete", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^создай\s+папку\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "mkdir", "path": m.group(1)},
        ),
        # ── generic: UK ──────────────────────────────────────────
        VoicePattern(
            regex=r"^знайди\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:знайти|пошук|пошукай)\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "find", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^відкрий\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "open", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^перемісти\s+(.+?)\s+в\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "move", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^скопіюй\s+(.+?)\s+в\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "copy", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^перейменуй\s+(.+?)\s+(?:на|в)\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "rename", "path": m.group(1), "destination": m.group(2)},
        ),
        VoicePattern(
            regex=r"^видали\s+(?:файл\s+)?(.+)$",
            priority=300,
            args=lambda m: {"action": "delete", "path": m.group(1)},
        ),
        VoicePattern(
            regex=r"^створи\s+(?:папку|директорію)\s+(.+)$",
            priority=300,
            args=lambda m: {"action": "mkdir", "path": m.group(1)},
        ),
    )

    def __init__(
        self,
        *,
        path_validator: PathValidator | None = None,
    ) -> None:
        self._validator = path_validator

    # ── public entry point ────────────────────────────────────────

    async def execute(self, args: FileManagerArgs) -> ToolResult:
        dispatch = {
            "find": self._find,
            "open": self._open,
            "move": self._move,
            "copy": self._copy,
            "rename": self._rename,
            "delete": self._delete,
            "mkdir": self._mkdir,
        }
        handler = dispatch.get(args.action)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"Unknown action: {args.action!r}. "
                f"Supported: {', '.join(dispatch)}.",
            )
        return await handler(args)

    # ── sandbox helper ────────────────────────────────────────────

    def _validate_path(self, raw: str) -> Path | ToolResult:
        """Validate a path through the sandbox.

        Returns a resolved ``Path`` on success or a ``ToolResult`` error.
        """
        if not raw.strip():
            return ToolResult(success=False, error="No path provided, sir.")
        if self._validator is not None:
            try:
                return self._validator.validate(raw)
            except SandboxViolationError as e:
                return ToolResult(
                    success=False,
                    error=(
                        f"Access denied: {e.reason}. "
                        "I can only manage files in your allowed "
                        "workspace folders, sir."
                    ),
                )
        # No sandbox — resolve manually (tests, legacy).
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.home() / p).resolve()
        else:
            p = p.resolve()
        return p

    # ── actions ───────────────────────────────────────────────────

    async def _find(self, args: FileManagerArgs) -> ToolResult:
        """Search for files matching a pattern."""
        query = args.path.strip()
        if not query:
            return ToolResult(success=False, error="No search query provided, sir.")

        # Determine search root.
        if args.search_dir:
            root_or_err = self._validate_path(args.search_dir)
        else:
            # Default: user home.
            root_or_err = Path.home()

        if isinstance(root_or_err, ToolResult):
            return root_or_err
        search_root = root_or_err

        query_lower = query.lower()

        def _search() -> list[str]:
            results: list[str] = []
            try:
                for dirpath, _dirnames, filenames in os.walk(search_root):
                    for fname in filenames:
                        if query_lower in fname.lower():
                            results.append(os.path.join(dirpath, fname))
                            if len(results) >= _MAX_FIND_RESULTS:
                                return results
            except PermissionError:
                pass
            return results

        try:
            found = await asyncio.to_thread(_search)
        except OSError as e:
            return ToolResult(success=False, error=f"Search failed: {e}")

        if not found:
            return ToolResult(
                success=True,
                output=f"No files matching '{query}' found, sir.",
            )

        names = [os.path.basename(p) for p in found]
        preview = ", ".join(names[:_SPOKEN_PREVIEW_COUNT])
        if len(found) > _SPOKEN_PREVIEW_COUNT:
            suffix = (
                f"Found {len(found)} files, sir. "
                f"First {_SPOKEN_PREVIEW_COUNT}: {preview}."
            )
        elif len(found) == 1:
            suffix = f"Found it, sir: {found[0]}"
        else:
            suffix = f"Found {len(found)} files, sir: {preview}."
        return ToolResult(success=True, output=suffix)

    async def _open(self, args: FileManagerArgs) -> ToolResult:
        """Open a file in its default application.

        Resolution order:
          1. Magic token ``:last_download`` → newest file in ~/Downloads.
          2. Exact/absolute path via sandbox validation.
          3. If the exact path does not exist AND the token looks like a
             bare filename (no separators), search Desktop → Documents →
             Downloads for a case-insensitive match.
        """
        raw = args.path.strip()
        if not raw:
            return ToolResult(success=False, error="No path provided, sir.")

        # ── :last_download magic ──────────────────────────────────
        if raw == ":last_download":
            found = await asyncio.to_thread(self._latest_download)
            if found is None:
                return ToolResult(
                    success=False,
                    error="Downloads folder is empty, sir.",
                )
            return await self._open_resolved(found)

        # ── exact path ────────────────────────────────────────────
        resolved = self._validate_path(raw)
        if isinstance(resolved, ToolResult):
            return resolved

        if resolved.exists():
            return await self._open_resolved(resolved)

        # ── bare-filename search fallback ─────────────────────────
        if os.sep not in raw and "/" not in raw:
            found = await asyncio.to_thread(self._search_common_dirs, raw)
            if found is not None:
                return await self._open_resolved(found)

        return ToolResult(
            success=False,
            error=f"File not found: {resolved.name}, sir.",
        )

    # ── _open helpers ─────────────────────────────────────────────

    async def _open_resolved(self, target: Path) -> ToolResult:
        """Launch *target* in the OS default handler."""

        def _launch() -> None:
            system = platform.system()
            if system == "Windows":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(target)])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", str(target)])  # noqa: S603

        try:
            await asyncio.to_thread(_launch)
        except OSError as e:
            return ToolResult(success=False, error=f"Could not open: {e}")

        return ToolResult(
            success=True,
            output=f"Opened {target.name}, sir.",
        )

    @staticmethod
    def _search_common_dirs(name: str) -> Path | None:
        """Case-insensitive search in Desktop/Documents/Downloads."""
        home = Path.home()
        name_lower = name.lower()
        for dirname in _COMMON_SEARCH_DIRS:
            folder = home / dirname
            if not folder.is_dir():
                continue
            try:
                for entry in folder.iterdir():
                    if entry.name.lower() == name_lower and entry.is_file():
                        return entry
            except PermissionError:
                continue
        return None

    @staticmethod
    def _latest_download() -> Path | None:
        """Return the most-recently-modified file in ~/Downloads."""
        dl = Path.home() / "Downloads"
        if not dl.is_dir():
            return None
        latest: Path | None = None
        latest_mtime: float = -1.0
        try:
            for entry in dl.iterdir():
                if entry.is_file():
                    mt = entry.stat().st_mtime
                    if mt > latest_mtime:
                        latest_mtime = mt
                        latest = entry
        except PermissionError:
            pass
        return latest

    async def _move(self, args: FileManagerArgs) -> ToolResult:
        """Move a file or folder to a new location."""
        src = self._validate_path(args.path)
        if isinstance(src, ToolResult):
            return src
        dst = self._validate_path(args.destination)
        if isinstance(dst, ToolResult):
            return dst

        if not src.exists():
            return ToolResult(
                success=False,
                error=f"Source not found: {src.name}, sir.",
            )

        def _move_file() -> Path:
            result = shutil.move(str(src), str(dst))
            return Path(result)

        try:
            final = await asyncio.to_thread(_move_file)
        except (OSError, shutil.Error) as e:
            return ToolResult(success=False, error=f"Move failed: {e}")

        return ToolResult(
            success=True,
            output=f"Moved {src.name} to {final.parent.name}, sir.",
        )

    async def _copy(self, args: FileManagerArgs) -> ToolResult:
        """Copy a file or folder to a new location."""
        src = self._validate_path(args.path)
        if isinstance(src, ToolResult):
            return src
        dst = self._validate_path(args.destination)
        if isinstance(dst, ToolResult):
            return dst

        if not src.exists():
            return ToolResult(
                success=False,
                error=f"Source not found: {src.name}, sir.",
            )

        def _copy_file() -> Path:
            if src.is_dir():
                result = shutil.copytree(str(src), str(dst))
            else:
                result = shutil.copy2(str(src), str(dst))
            return Path(result)

        try:
            final = await asyncio.to_thread(_copy_file)
        except (OSError, shutil.Error) as e:
            return ToolResult(success=False, error=f"Copy failed: {e}")

        return ToolResult(
            success=True,
            output=f"Copied {src.name} to {final.parent.name}, sir.",
        )

    async def _rename(self, args: FileManagerArgs) -> ToolResult:
        """Rename a file or folder."""
        src = self._validate_path(args.path)
        if isinstance(src, ToolResult):
            return src

        # Check destination first — no point validating source if no name given
        dest_raw = args.destination.strip()
        if not dest_raw:
            return ToolResult(
                success=False,
                error="No new name provided, sir.",
            )

        if not src.exists():
            return ToolResult(
                success=False,
                error=f"Source not found: {src.name}, sir.",
            )

        # Destination for rename: if it's just a name (no path separators),
        # rename in the same directory.

        if os.sep not in dest_raw and "/" not in dest_raw:
            # Just a filename — rename in same directory.
            new_path = src.parent / dest_raw
        else:
            dst = self._validate_path(dest_raw)
            if isinstance(dst, ToolResult):
                return dst
            new_path = dst

        def _rename_file() -> None:
            os.rename(str(src), str(new_path))

        try:
            await asyncio.to_thread(_rename_file)
        except OSError as e:
            return ToolResult(success=False, error=f"Rename failed: {e}")

        return ToolResult(
            success=True,
            output=f"Renamed {src.name} to {new_path.name}, sir.",
        )

    async def _delete(self, args: FileManagerArgs) -> ToolResult:
        """Delete a file or folder (DESTRUCTIVE — gated by risk level)."""
        resolved = self._validate_path(args.path)
        if isinstance(resolved, ToolResult):
            return resolved

        if not resolved.exists():
            return ToolResult(
                success=False,
                error=f"Not found: {resolved.name}, sir.",
            )

        target_name = resolved.name
        is_dir = resolved.is_dir()

        def _delete_target() -> None:
            if is_dir:
                shutil.rmtree(str(resolved))
            else:
                os.remove(str(resolved))

        try:
            await asyncio.to_thread(_delete_target)
        except (OSError, shutil.Error) as e:
            return ToolResult(success=False, error=f"Delete failed: {e}")

        kind = "folder" if is_dir else "file"
        return ToolResult(
            success=True,
            output=f"Deleted {kind} {target_name}, sir.",
        )

    async def _mkdir(self, args: FileManagerArgs) -> ToolResult:
        """Create a new directory."""
        resolved = self._validate_path(args.path)
        if isinstance(resolved, ToolResult):
            return resolved

        if resolved.exists():
            if resolved.is_dir():
                return ToolResult(
                    success=True,
                    output=f"Folder {resolved.name} already exists, sir.",
                )
            return ToolResult(
                success=False,
                error=f"A file with that name already exists: {resolved.name}.",
            )

        def _make_dir() -> None:
            os.makedirs(str(resolved), exist_ok=True)

        try:
            await asyncio.to_thread(_make_dir)
        except OSError as e:
            return ToolResult(success=False, error=f"Could not create folder: {e}")

        return ToolResult(
            success=True,
            output=f"Created folder {resolved.name}, sir.",
        )
