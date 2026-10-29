"""Read-only filesystem helpers.

This is the Phase 4 file surface: list_directory ONLY. Read, write, move,
and delete are intentionally absent — they're the destructive class of
operations that needs the requires_confirmation UX before they ship.

Phase 5 (T5.1): all paths are validated through the workspace sandbox
before any I/O. Access outside the whitelist is blocked with a clear
error message."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult
from jarvis.tools.risk import RiskLevel
from jarvis.tools.sandbox import PathValidator, SandboxViolationError

log = logging.getLogger(__name__)

# How many entry names get spoken back. Voice playback of a full file
# listing is unusable — five names plus a count is the whole UX a user
# can absorb in a single TTS pass. The screen / settings view is the
# place for the full list; this tool is voice-only.
_SPOKEN_PREVIEW_COUNT = 5


class ListDirectoryArgs(BaseModel):
    path: str = Field(
        description=(
            "Directory to list. Accepts ~ for the user home. "
            "Relative paths resolve against the user home."
        ),
    )


class ListDirectoryTool:
    name: str = "list_directory"
    description: str = (
        "Lists files in a directory. Only use when the user explicitly "
        "asks what is in a folder, directory, or path. Read-only — does "
        "not open, modify, or delete files. "
        "(RU: покажи файлы, список файлов / UK: покажи файли)"
    )
    args_schema = ListDirectoryArgs
    requires_confirmation: bool = False
    risk_level: RiskLevel = RiskLevel.READ

    def __init__(
        self,
        *,
        path_validator: PathValidator | None = None,
    ) -> None:
        self._validator = path_validator

    async def execute(self, args: ListDirectoryArgs) -> ToolResult:
        # T5.1: sandbox validation before any filesystem access.
        if self._validator is not None:
            try:
                resolved = self._validator.validate(args.path)
            except SandboxViolationError as e:
                return ToolResult(
                    success=False,
                    error=(
                        f"Access denied: {e.reason}. "
                        "I can only browse directories in your allowed "
                        "workspace folders, sir."
                    ),
            )
        else:
            # No validator → fail-closed.
            return ToolResult(
                success=False,
                error=(
                    "Directory listing is disabled: no sandbox validator "
                    "is configured, sir."
                ),
            )

        def _list() -> tuple[Path, list[str]]:
            target = resolved
            if not target.exists():
                raise FileNotFoundError(str(target))
            if not target.is_dir():
                raise NotADirectoryError(str(target))
            return target, sorted(p.name for p in target.iterdir())

        try:
            actual, names = await asyncio.to_thread(_list)
        except FileNotFoundError as e:
            return ToolResult(success=False, error=f"no such directory: {e}")
        except NotADirectoryError as e:
            return ToolResult(success=False, error=f"not a directory: {e}")
        except PermissionError as e:
            return ToolResult(success=False, error=f"permission denied: {e}")
        except OSError as e:
            return ToolResult(success=False, error=f"could not list: {e}")

        if not names:
            log.info("list_directory: %s is empty", actual)
            return ToolResult(success=True, output="The folder is empty, sir.")
        preview = ", ".join(names[:_SPOKEN_PREVIEW_COUNT])
        suffix = (
            f"First {_SPOKEN_PREVIEW_COUNT}: {preview}."
            if len(names) > _SPOKEN_PREVIEW_COUNT
            else preview + "."
        )
        return ToolResult(
            success=True,
            output=f"{len(names)} items, sir. {suffix}",
        )
