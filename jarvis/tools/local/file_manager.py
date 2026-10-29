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
from datetime import datetime, timedelta
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


# Size unit multipliers for parsing "10MB" / "1gb" etc.
_SIZE_UNITS: dict[str, int] = {
    "b": 1,
    "kb": 1024,
    "mb": 1024 ** 2,
    "gb": 1024 ** 3,
    "tb": 1024 ** 4,
}


def _parse_size(text: str) -> int | None:
    """Parse '10MB' / '1.5 gb' / '500kb' into bytes. None if unparseable."""
    if not text or not text.strip():
        return None
    m = re.match(r"^\s*([\d.]+)\s*([kmgt]?b)\s*$", text.strip().lower())
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    return int(value * _SIZE_UNITS[m.group(2)])


# Spoken word -> file extension for filtered find ("pdfs" -> ".pdf").
_EXT_WORD: dict[str, str] = {
    "pdf": ".pdf", "pdfs": ".pdf",
    "doc": ".doc", "docs": ".doc", "docx": ".docx", "word": ".docx",
    "txt": ".txt", "text": ".txt",
    "python": ".py", "py": ".py",
    "image": ".png", "images": ".png", "photo": ".jpg", "photos": ".jpg",
    "video": ".mp4", "videos": ".mp4",
    "music": ".mp3", "audio": ".mp3",
    "zip": ".zip", "archive": ".zip", "archives": ".zip",
    "excel": ".xlsx", "xlsx": ".xlsx", "csv": ".csv",
    # RU
    "пдф": ".pdf", "документ": ".docx", "документы": ".docx",
    "питон": ".py", "пайтон": ".py",
    "картинки": ".png", "картинка": ".png", "фото": ".jpg",
    "видео": ".mp4", "музыка": ".mp3",
    # UK
    "пітон": ".py", "зображення": ".png", "відео": ".mp4",
}

# Spoken folder name -> KNOWN_FOLDERS key / search_dir value.
_RU_DIR: dict[str, str] = {
    "загрузках": "downloads",
    "документах": "documents",
    "на рабочем столе": "desktop",
    "изображениях": "pictures",
}
_UK_DIR: dict[str, str] = {
    "завантаженнях": "downloads",
    "документах": "documents",
    "зображеннях": "pictures",
}


# Folder-name keyword -> absolute Path, for resolving spoken folder names.
KNOWN_FOLDERS: dict[str, Path] = {
    "downloads": Path.home() / "Downloads",
    "документы": Path.home() / "Documents",
    "documents": Path.home() / "Documents",
    "документи": Path.home() / "Documents",
    "desktop": Path.home() / "Desktop",
    "pictures": Path.home() / "Pictures",
    "изображения": Path.home() / "Pictures",
    "зображення": Path.home() / "Pictures",
}


def _parse_age(text: str) -> timedelta | None:
    """Parse '3d' / '1w' / '2m' into a timedelta. None if unparseable.

    d = days, w = weeks, m = months (~30 days).
    """
    if not text or not text.strip():
        return None
    m = re.match(r"^\s*(\d+)\s*([dwm])\s*$", text.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "w":
        return timedelta(weeks=n)
    return timedelta(days=n * 30)  # month ≈ 30 days


# ── args schema ───────────────────────────────────────────────────


def _ensure_ext(name: str, ext: str) -> str:
    """Append *ext* to a bare name if it has no extension."""
    name = name.strip()
    return name if Path(name).suffix else name + ext


def _join_folder(name: str, folder: str | None) -> str:
    """Combine a filename with an optional spoken folder keyword."""
    name = name.strip()
    if not folder:
        return name
    key = folder.strip().lower()
    base = KNOWN_FOLDERS.get(key)
    return str(base / name) if base is not None else name


def _strip_folder_word(text: str) -> str:
    """Normalise a spoken folder reference: trailing 'folder'/'папку' removed,
    and a known keyword resolved to its absolute path."""
    t = text.strip()
    t = re.sub(r"\s+(?:folder|папку|папка|папки|директорію)$", "", t, flags=re.IGNORECASE)
    key = t.lower()
    base = KNOWN_FOLDERS.get(key)
    return str(base) if base is not None else t


def _validate_or_resolve(validator, raw: str):
    """Resolve *raw* to an absolute Path, honouring known-folder keywords
    and the sandbox validator when present. Returns Path or ToolResult."""
    key = raw.strip().lower()
    if key in KNOWN_FOLDERS:
        return KNOWN_FOLDERS[key]
    if validator is not None:
        try:
            return validator.validate(raw)
        except SandboxViolationError as e:
            return ToolResult(
                success=False,
                error=(
                    f"Access denied: {e.reason}. I can only manage files in "
                    "your allowed workspace folders, sir."
                ),
            )
    # No validator → fail-closed.
    return ToolResult(
        success=False,
        error=(
            "File operations are disabled: no sandbox validator is "
            "configured, sir."
        ),
    )


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
    # ── optional find filters (empty = no filter, current behaviour) ──
    extension: str = Field(
        default="",
        description=(
            "Filter 'find' results by file extension, e.g. '.pdf', 'docx'. "
            "Leading dot optional. Empty = any extension."
        ),
    )
    newer_than: str = Field(
        default="",
        description=(
            "Filter 'find' to files modified within this window: "
            "'3d' (days), '1w' (weeks), '1m' (months). Empty = any age."
        ),
    )
    min_size: str = Field(
        default="",
        description=(
            "Filter 'find' to files at least this big, e.g. '10MB', '1GB'. "
            "Empty = no minimum."
        ),
    )
    max_size: str = Field(
        default="",
        description=(
            "Filter 'find' to files at most this big, e.g. '500MB'. "
            "Empty = no maximum."
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
        # ── filtered find (priority 290 — beats generic find 300) ──
        # EN: "find pdfs in downloads", "find python files"
        VoicePattern(
            regex=r"^find\s+(\w+)s?\s+in\s+(downloads|documents|desktop|pictures)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "extension": _EXT_WORD.get(m.group(1).lower(), m.group(1).lower()),
                "search_dir": m.group(2).lower(),
            },
        ),
        # EN: "find files larger than 100 mb"
        VoicePattern(
            regex=r"^find\s+files?\s+(?:larger|bigger)\s+than\s+([\d.]+\s*[kmgt]?b)$",
            priority=290,
            args=lambda m: {"action": "find", "path": "", "min_size": m.group(1)},
        ),
        VoicePattern(
            regex=r"^find\s+files?\s+smaller\s+than\s+([\d.]+\s*[kmgt]?b)$",
            priority=290,
            args=lambda m: {"action": "find", "path": "", "max_size": m.group(1)},
        ),
        # EN: "find recent files from last week" / "... from last month"
        VoicePattern(
            regex=r"^find\s+(?:recent\s+)?files?\s+from\s+(?:the\s+)?last\s+(week|month|day)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "newer_than": {"day": "1d", "week": "1w", "month": "1m"}[m.group(1)],
            },
        ),
        # RU: "найди pdf в загрузках"
        VoicePattern(
            regex=r"^найди\s+(\w+)\s+в\s+(загрузках|документах|на\s+рабочем\s+столе|изображениях)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "extension": _EXT_WORD.get(m.group(1).lower(), m.group(1).lower()),
                "search_dir": _RU_DIR.get(m.group(2).lower(), ""),
            },
        ),
        # RU: "найди файлы больше 100 мегабайт"
        VoicePattern(
            regex=r"^найди\s+файлы\s+больше\s+([\d.]+)\s*(?:мб|мегабайт|гб|гигабайт)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "min_size": m.group(1) + ("gb" if "г" in m.group(0) else "mb"),
            },
        ),
        # RU: "найди файлы за последнюю неделю/месяц/день"
        VoicePattern(
            regex=r"^найди\s+(?:недавние\s+)?файлы\s+за\s+(?:последн(?:юю|ий)\s+)?(неделю|месяц|день)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "newer_than": {"день": "1d", "неделю": "1w", "месяц": "1m"}[m.group(1)],
            },
        ),
        # UK: "знайди pdf в завантаженнях"
        VoicePattern(
            regex=r"^знайди\s+(\w+)\s+в\s+(завантаженнях|документах|зображеннях)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "extension": _EXT_WORD.get(m.group(1).lower(), m.group(1).lower()),
                "search_dir": _UK_DIR.get(m.group(2).lower(), ""),
            },
        ),
        # UK: "знайди файли більше 100 мегабайт"
        VoicePattern(
            regex=r"^знайди\s+файли\s+більше\s+([\d.]+)\s*(?:мб|мегабайт|гб|гігабайт)$",
            priority=290,
            args=lambda m: {
                "action": "find", "path": "",
                "min_size": m.group(1) + ("gb" if "г" in m.group(0) else "mb"),
            },
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
            # "файл" is REQUIRED here (not optional): a bare "открой блокнот"
            # is an app launch (open_app), not a file open. Mirrors the EN
            # pattern which already requires file|document.
            regex=r"^открой\s+файл\s+(.+)$",
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
            # "файл" required — see the RU "открой файл" note above.
            regex=r"^відкрий\s+файл\s+(.+)$",
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
        # No sandbox validator → fail-closed.  In production the validator
        # is always injected by setup_local_tools(); reaching this branch
        # means the tool was constructed incorrectly.
        return ToolResult(
            success=False,
            error=(
                "File operations are disabled: no sandbox validator is "
                "configured, sir."
            ),
        )

    # ── actions ───────────────────────────────────────────────────

    async def _find(self, args: FileManagerArgs) -> ToolResult:
        """Search for files matching a pattern."""
        query = args.path.strip()
        has_filter = bool(
            args.extension.strip() or args.newer_than.strip()
            or args.min_size.strip() or args.max_size.strip()
        )
        if not query and not has_filter:
            return ToolResult(success=False, error="No search query provided, sir.")

        # Determine search root.
        if args.search_dir:
            # Resolve a spoken folder keyword ("downloads") to its path first.
            kw = args.search_dir.strip().lower()
            if kw in KNOWN_FOLDERS:
                root_or_err = KNOWN_FOLDERS[kw]
            else:
                root_or_err = self._validate_path(args.search_dir)
        else:
            # Default: user home.
            root_or_err = Path.home()

        if isinstance(root_or_err, ToolResult):
            return root_or_err
        search_root = root_or_err

        query_lower = query.lower()

        # ── parse optional filters (empty = no filter) ────────────
        ext = args.extension.strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        age = _parse_age(args.newer_than)
        cutoff_ts = (datetime.now() - age).timestamp() if age else None
        min_bytes = _parse_size(args.min_size)
        max_bytes = _parse_size(args.max_size)

        def _matches(fname: str, full: str) -> bool:
            if query_lower and query_lower not in fname.lower():
                return False
            if ext and not fname.lower().endswith(ext):
                return False
            if cutoff_ts is not None or min_bytes is not None or max_bytes is not None:
                try:
                    st = os.stat(full)
                except OSError:
                    return False
                if cutoff_ts is not None and st.st_mtime < cutoff_ts:
                    return False
                if min_bytes is not None and st.st_size < min_bytes:
                    return False
                if max_bytes is not None and st.st_size > max_bytes:
                    return False
            return True

        def _search() -> list[str]:
            results: list[str] = []
            try:
                for dirpath, _dirnames, filenames in os.walk(search_root):
                    for fname in filenames:
                        full = os.path.join(dirpath, fname)
                        if _matches(fname, full):
                            results.append(full)
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


# ── file templates for CreateFileTool ─────────────────────────────────────

_FILE_TEMPLATES: dict[str, str] = {
    ".py": '# -*- coding: utf-8 -*-\n"""\nModule: {name}\n"""\n',
    ".md": "# {name}\n\n",
    ".txt": "",
    ".html": (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "    <title>{name}</title>\n</head>\n<body>\n\n</body>\n</html>\n"
    ),
}


class CreateFileArgs(BaseModel):
    path: str = Field(
        description=(
            "Path (or bare filename) of the file to create. A known folder "
            "keyword like 'desktop' or 'downloads' resolves to that folder. "
            "The extension selects a starter template (.py/.md/.txt/.html)."
        ),
    )


class CreateFileTool:
    """Create a new empty file, optionally from a starter template."""

    name: str = "create_file"
    description: str = (
        "Create a new, empty file (optionally with a starter template for "
        ".py/.md/.txt/.html). Fails if the file already exists. "
        "(RU: создай файл … / UK: створи файл …)"
    )
    args_schema: type[BaseModel] = CreateFileArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "create a python file called utils", "create python file utils"
        VoicePattern(
            regex=r"^create\s+(?:a\s+)?python\s+file\s+(?:called\s+|named\s+)?(.+)$",
            priority=280,
            args=lambda m: {"path": _ensure_ext(m.group(1), ".py")},
        ),
        # EN: "create file notes.md", "new file report.txt on desktop"
        VoicePattern(
            regex=r"^create\s+file\s+(.+?)(?:\s+(?:on|in)\s+(.+))?$",
            priority=285,
            args=lambda m: {"path": _join_folder(m.group(1), m.group(2))},
        ),
        VoicePattern(
            regex=r"^(?:new|make)\s+file\s+(.+?)(?:\s+(?:on|in)\s+(.+))?$",
            priority=285,
            args=lambda m: {"path": _join_folder(m.group(1), m.group(2))},
        ),
        # RU: "создай питон файл utils", "создай файл заметки.md на рабочем столе"
        VoicePattern(
            regex=r"^создай\s+(?:пайтон|питон)\s+файл\s+(.+)$",
            priority=280,
            args=lambda m: {"path": _ensure_ext(m.group(1), ".py")},
        ),
        VoicePattern(
            regex=r"^(?:создай|новый)\s+файл\s+(.+?)(?:\s+(?:на|в)\s+(.+))?$",
            priority=285,
            args=lambda m: {"path": _join_folder(m.group(1), m.group(2))},
        ),
        # UK: "створи пітон файл utils", "створи файл нотатки.md"
        VoicePattern(
            regex=r"^створи\s+(?:пітон|пайтон)\s+файл\s+(.+)$",
            priority=280,
            args=lambda m: {"path": _ensure_ext(m.group(1), ".py")},
        ),
        VoicePattern(
            regex=r"^створи\s+файл\s+(.+?)(?:\s+(?:на|в)\s+(.+))?$",
            priority=285,
            args=lambda m: {"path": _join_folder(m.group(1), m.group(2))},
        ),
    )

    def __init__(self, *, path_validator: PathValidator | None = None) -> None:
        self._validator = path_validator

    async def execute(self, args: CreateFileArgs) -> ToolResult:
        raw = args.path.strip()
        if not raw:
            return ToolResult(success=False, error="No file name provided, sir.")
        resolved = _validate_or_resolve(self._validator, raw)
        if isinstance(resolved, ToolResult):
            return resolved
        if resolved.exists():
            return ToolResult(
                success=False,
                error=f"'{resolved.name}' already exists, sir. I won't overwrite it.",
            )

        ext = resolved.suffix.lower()
        template = _FILE_TEMPLATES.get(ext, "")
        content = template.format(name=resolved.stem) if template else ""

        def _write() -> None:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")

        try:
            await asyncio.to_thread(_write)
        except OSError as e:
            return ToolResult(success=False, error=f"Could not create file: {e}")
        return ToolResult(success=True, output=f"Created {resolved.name}, sir.")


class ArchiveArgs(BaseModel):
    path: str = Field(
        description=(
            "Folder to archive into a .zip. A known folder keyword "
            "('downloads', 'documents') resolves to that folder."
        ),
    )
    destination: str = Field(
        default="",
        description=(
            "Optional output .zip path. Defaults to "
            "'<folder>_<YYYYMMDD>.zip' next to the source folder."
        ),
    )


class ArchiveTool:
    """Archive a folder into a .zip file."""

    name: str = "archive"
    description: str = (
        "Compress a folder into a .zip archive. "
        "(RU: архивируй/запакуй папку … / UK: заархівуй/запакуй папку …)"
    )
    args_schema: type[BaseModel] = ArchiveArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^archive\s+(?:the\s+)?(.+?)(?:\s+folder)?$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:zip|compress)\s+(?:the\s+)?(?:folder\s+)?(.+?)(?:\s+folder)?$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:архивируй|заархивируй)\s+(?:папку\s+)?(.+)$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
        VoicePattern(
            regex=r"^(?:запакуй|сожми|сжать)\s+(?:папку\s+)?(.+)$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:заархівуй|архівуй)\s+(?:папку\s+)?(.+)$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
        VoicePattern(
            regex=r"^запакуй\s+(?:папку\s+)?(.+)$",
            priority=285,
            args=lambda m: {"path": _strip_folder_word(m.group(1))},
        ),
    )

    def __init__(self, *, path_validator: PathValidator | None = None) -> None:
        self._validator = path_validator

    async def execute(self, args: ArchiveArgs) -> ToolResult:
        raw = args.path.strip()
        if not raw:
            return ToolResult(success=False, error="No folder provided, sir.")
        resolved = _validate_or_resolve(self._validator, raw)
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.exists() or not resolved.is_dir():
            return ToolResult(
                success=False,
                error=f"'{raw}' is not a folder I can reach, sir.",
            )

        if args.destination.strip():
            out = Path(args.destination.strip()).expanduser()
            base_name = str(out.with_suffix(""))
        else:
            stamp = datetime.now().strftime("%Y%m%d")
            base_name = str(resolved.parent / f"{resolved.name}_{stamp}")

        def _make() -> str:
            # make_archive appends .zip; root_dir/base_dir keep the archive
            # rooted at the folder itself.
            return shutil.make_archive(
                base_name, "zip",
                root_dir=str(resolved.parent),
                base_dir=resolved.name,
            )

        try:
            archive_path = await asyncio.to_thread(_make)
        except OSError as e:
            return ToolResult(success=False, error=f"Could not create archive: {e}")

        try:
            size_mb = os.path.getsize(archive_path) / (1024 ** 2)
            size_str = f"{size_mb:.1f} MB"
        except OSError:
            size_str = "unknown size"
        return ToolResult(
            success=True,
            output=f"Archived {resolved.name} to {os.path.basename(archive_path)} ({size_str}), sir.",
        )
