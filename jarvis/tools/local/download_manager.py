"""Download files from URLs to the local filesystem.

Downloads go to ~/Downloads by default (or a user-specified folder).
Progress is logged but not streamed to TTS. Uses requests (lazy import).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

_DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"


def _filename_from_url(url: str) -> str:
    """Extract a sensible filename from a URL."""
    parsed = urlparse(url)
    path_part = unquote(parsed.path).rstrip("/")
    name = path_part.rsplit("/", 1)[-1] if "/" in path_part else path_part
    if not name or name == "/" or "." not in name:
        name = "download"
    return name


def _download_file(url: str, dest_dir: Path, filename: str | None = None) -> dict:
    """Synchronous download using requests."""
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests library not installed."}

    if not filename:
        filename = _filename_from_url(url)

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    # Avoid overwriting: append (1), (2), etc.
    base, ext = os.path.splitext(filename)
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{base} ({counter}){ext}"
        counter += 1

    log.info("Downloading %s -> %s", url, dest_path)
    start = time.monotonic()

    try:
        resp = requests.get(url, stream=True, timeout=60, allow_redirects=True)
        resp.raise_for_status()

        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)

        elapsed = time.monotonic() - start
        size = dest_path.stat().st_size

        log.info(
            "Downloaded %s (%s bytes) in %.1fs",
            dest_path.name, f"{size:,}", elapsed,
        )
        return {
            "success": True,
            "path": str(dest_path),
            "filename": dest_path.name,
            "size_bytes": size,
            "elapsed_sec": round(elapsed, 1),
        }
    except requests.exceptions.RequestException as e:
        # Clean up partial file.
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


class DownloadArgs(BaseModel):
    url: str = Field(description="URL of the file to download.")
    filename: str = Field(
        default="",
        description="Override filename. Auto-detected from URL if empty.",
    )
    directory: str = Field(
        default="",
        description=(
            "Destination directory. Defaults to ~/Downloads if empty."
        ),
    )


class DownloadFileTool:
    name: str = "download_file"
    description: str = (
        "Downloads a file from a URL to the local computer. "
        "Saves to ~/Downloads by default; you can specify a different directory. "
        "Auto-detects filename from URL. "
        "(RU: скачай файл, загрузи файл / UK: скачай файл, завантаж файл)"
    )
    args_schema = DownloadArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "download <url>"
        VoicePattern(
            regex=r"^download\s+(https?://\S+)$",
            priority=200,
            args=lambda m: {"url": m.group(1)},
        ),
        # RU: "скачай <url>", "загрузи <url>"
        VoicePattern(
            regex=r"^(?:скачай|загрузи)\s+(?:файл\s+)?(https?://\S+)$",
            priority=200,
            args=lambda m: {"url": m.group(1)},
        ),
        # UK: "скачай <url>", "завантаж <url>"
        VoicePattern(
            regex=r"^(?:скачай|завантаж)\s+(?:файл\s+)?(https?://\S+)$",
            priority=200,
            args=lambda m: {"url": m.group(1)},
        ),
    )

    async def execute(self, args: DownloadArgs) -> ToolResult:
        dest_dir = Path(args.directory) if args.directory else _DEFAULT_DOWNLOAD_DIR
        filename = args.filename or None

        try:
            result = await asyncio.to_thread(
                _download_file, args.url, dest_dir, filename,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Download failed: {e}")

        if result.get("success"):
            size_mb = result.get("size_bytes", 0) / (1024 * 1024)
            elapsed = result.get("elapsed_sec", 0)
            return ToolResult(
                success=True,
                output=(
                    f"Downloaded '{result['filename']}' "
                    f"({size_mb:.1f} MB, {elapsed}s) to {result['path']}, sir."
                ),
            )
        return ToolResult(success=False, error=result.get("error", "Download failed."))
