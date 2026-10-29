"""Summarise a YouTube video from its subtitles or description.

Downloads subtitles via yt-dlp (lazy import), sends them to a local LLM
for summarisation, and returns key points.

Dependencies (all optional, checked at runtime):
    - yt-dlp for subtitle/metadata extraction
    - ollama_client for LLM summarisation
    - browser_controller (Selenium) for current tab URL (optional)

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})",
)


def _is_youtube_url(url: str) -> bool:
    """Return True if *url* looks like a YouTube video URL."""
    return bool(_YT_URL_RE.search(url))


def _extract_video_id(url: str) -> str:
    """Extract the 11-char video ID from a YouTube URL, or empty string."""
    m = _YT_URL_RE.search(url)
    return m.group(1) if m else ""


def _download_subtitles(url: str) -> dict[str, Any]:
    """Download subtitles / info via yt-dlp.

    Returns {"success": True, "text": <subtitle text>, "title": <video title>}
    or {"success": False, "error": ...}.
    """
    try:
        import yt_dlp  # noqa: F811
    except ImportError:
        return {
            "success": False,
            "error": (
                "yt-dlp is not installed. "
                "Install it with: pip install yt-dlp"
            ),
        }

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_template = str(Path(tmp_dir) / "subs")

        # First pass: try to get subtitles.
        ydl_opts: dict[str, Any] = {
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": ["en", "ru", "uk"],
            "subtitlesformat": "vtt",
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
        }

        title = ""
        description = ""

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "")
                description = info.get("description", "")
        except Exception as exc:
            return {"success": False, "error": f"yt-dlp error: {exc}"}

        # Look for downloaded subtitle files.
        sub_text = _read_subtitle_files(Path(tmp_dir))

        if sub_text:
            return {"success": True, "text": sub_text[:15000], "title": title}

        # No subtitles — use the video description as a fallback.
        if description:
            return {
                "success": True,
                "text": description[:5000],
                "title": title,
            }

        return {
            "success": False,
            "error": "No subtitles or description available for this video.",
        }


def _read_subtitle_files(directory: Path) -> str:
    """Read and clean VTT/SRT subtitle files from *directory*."""
    text_parts: list[str] = []

    for ext in ("*.vtt", "*.srt"):
        for sub_file in directory.glob(ext):
            try:
                raw = sub_file.read_text(encoding="utf-8", errors="replace")
                cleaned = _clean_subtitle_text(raw)
                if cleaned:
                    text_parts.append(cleaned)
            except Exception:
                log.debug("Failed to read subtitle file %s", sub_file, exc_info=True)

    return "\n".join(text_parts)


def _clean_subtitle_text(raw: str) -> str:
    """Remove VTT/SRT timestamps and formatting tags, deduplicate lines."""
    # Remove WEBVTT header.
    raw = re.sub(r"^WEBVTT.*?\n\n", "", raw, flags=re.DOTALL)
    # Remove timestamp lines.
    raw = re.sub(
        r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}.*",
        "",
        raw,
    )
    # Remove numeric cue IDs (SRT).
    raw = re.sub(r"^\d+$", "", raw, flags=re.MULTILINE)
    # Remove VTT formatting tags.
    raw = re.sub(r"<[^>]+>", "", raw)
    # Deduplicate consecutive identical lines.
    lines = raw.strip().splitlines()
    deduped: list[str] = []
    prev = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev:
            deduped.append(stripped)
            prev = stripped
    return " ".join(deduped)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class YoutubeSummaryArgs(BaseModel):
    url: str = Field(
        default="",
        description=(
            "YouTube video URL. If empty, reads the current browser tab "
            "(requires Selenium with a YouTube tab open)."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class YoutubeSummaryTool:
    """Summarise a YouTube video using subtitles and LLM."""

    name: str = "youtube_summary"
    description: str = (
        "Summarises a YouTube video by downloading its subtitles and "
        "extracting key points via LLM. Provide a YouTube URL or leave "
        "empty to use the current browser tab. "
        "(RU: о чём это видео, суммаризируй видео / "
        "UK: про що це відео, підсумуй відео)"
    )
    args_schema = YoutubeSummaryArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN — 4 patterns
        VoicePattern(
            regex=r"^summarize?\s+(?:this\s+)?video$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^what(?:'s| is)\s+this\s+video\s+about\??$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^video\s+summary$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^what\s+does\s+this\s+video\s+say\??$",
            priority=475,
            args=lambda m: {},
        ),
        # RU — 4 patterns
        VoicePattern(
            regex=r"^о\s+чём\s+(?:это\s+)?видео\??$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^суммаризируй\s+видео$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^краткое\s+содержание\s+видео$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^что\s+в\s+этом\s+видео\??$",
            priority=475,
            args=lambda m: {},
        ),
        # UK — 4 patterns
        VoicePattern(
            regex=r"^про\s+що\s+(?:це\s+)?відео\??$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^підсумуй\s+відео$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^коротко\s+про\s+відео$",
            priority=475,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^що\s+в\s+цьому\s+відео\??$",
            priority=475,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        ollama_client: Any = None,
        browser_controller: Any = None,
    ) -> None:
        self._ollama = ollama_client
        self._browser_controller = browser_controller

    async def execute(self, args: YoutubeSummaryArgs) -> ToolResult:
        """Main entry: resolve URL → download subs → summarise via LLM."""
        if not self._ollama:
            return ToolResult(
                success=False,
                error="Video summarisation requires an LLM client, but none is available.",
            )

        url = args.url.strip()

        # If no URL given, try current browser tab.
        if not url:
            url = self._detect_youtube_url()

        if not url or not _is_youtube_url(url):
            return ToolResult(
                success=False,
                error=(
                    "No YouTube URL provided and no YouTube tab is active. "
                    "Please provide a YouTube video URL."
                ),
            )

        # Download subtitles (potentially slow — run in thread).
        sub_result = await asyncio.to_thread(_download_subtitles, url)

        if not sub_result.get("success"):
            return ToolResult(
                success=False, error=sub_result.get("error", "Failed to get subtitles.")
            )

        sub_text = sub_result["text"]
        video_title = sub_result.get("title", "")

        # Summarise via LLM.
        summary = await self._summarise_with_llm(sub_text, video_title)
        if not summary:
            return ToolResult(
                success=False,
                error="LLM summarisation failed.",
            )

        header = f"Summary of \"{video_title}\":\n\n" if video_title else ""
        return ToolResult(success=True, output=f"{header}{summary}")

    def _detect_youtube_url(self) -> str:
        """Try to get YouTube URL from the current browser tab."""
        bc = self._browser_controller
        if bc is None or not getattr(bc, "driver_active", False):
            return ""
        try:
            url_result = bc.get_current_url()
            url = url_result.get("url", "")
            return url if _is_youtube_url(url) else ""
        except Exception:
            return ""

    async def _summarise_with_llm(
        self, text: str, title: str
    ) -> str:
        """Send subtitle text to the LLM for summarisation."""
        prompt = (
            "Summarise the following video"
            + (f' titled "{title}"' if title else "")
            + ". List the key points as bullet points.\n\n"
            + text[:12000]
        )

        try:
            response = await self._ollama.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            return response.get("message", {}).get("content", "").strip()
        except Exception as exc:
            log.warning("LLM summarisation failed: %s", exc)
            return ""
