"""Page Translation — translate a web page via LLM.

Reads the current page text (Selenium or httpx fallback), sends it
to the local LLM in chunks for translation, and returns the translated
text for TTS read-back or display.

Long pages are split into chunks to stay within the LLM context window.

Dependencies (all optional, checked at runtime):
    - browser_controller (Selenium) for live page text
    - content_reader helpers (httpx + BeautifulSoup) as fallback
    - ollama_client for LLM translation

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

_MAX_PAGE_CHARS = 15_000
_CHUNK_SIZE = 3000  # chars per LLM request


# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------

_LANG_ALIASES: dict[str, str] = {
    # EN
    "english": "English",
    "russian": "Russian",
    "ukrainian": "Ukrainian",
    "german": "German",
    "french": "French",
    "spanish": "Spanish",
    "polish": "Polish",
    "japanese": "Japanese",
    "chinese": "Chinese",
    # RU
    "русский": "Russian",
    "английский": "English",
    "украинский": "Ukrainian",
    "немецкий": "German",
    "французский": "French",
    "испанский": "Spanish",
    "польский": "Polish",
    "японский": "Japanese",
    "китайский": "Chinese",
    # UK
    "українська": "Ukrainian",
    "англійська": "English",
    "російська": "Russian",
    "німецька": "German",
    "французька": "French",
    "іспанська": "Spanish",
    "польська": "Polish",
    "японська": "Japanese",
    "китайська": "Chinese",
}


def _normalize_language(raw: str) -> str:
    """Normalize a free-form language name to a canonical label."""
    lower = raw.strip().lower()
    return _LANG_ALIASES.get(lower, raw.strip().title())


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class TranslatePageArgs(BaseModel):
    url: str = Field(
        default="",
        description=(
            "URL to translate. If empty, reads the current browser tab."
        ),
    )
    target_language: str = Field(
        default="English",
        description="Target language for translation (e.g. Russian, English, Ukrainian).",
    )
    save_to_file: bool = Field(
        default=False,
        description="If True, save the translation to ~/.jarvis/translations/.",
    )


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------

async def _get_page_text(
    url: str, browser_controller: Any | None,
) -> tuple[str, str, str | None]:
    """Return (text, resolved_url, error)."""
    # Try Selenium.
    if browser_controller is not None and browser_controller.driver_active:
        if not url:
            res = browser_controller.get_page_text(max_chars=_MAX_PAGE_CHARS)
            if res.get("success"):
                url_res = browser_controller.get_current_url()
                return res["text"], url_res.get("url", ""), None
        elif url:
            try:
                browser_controller.open_tab(url)
                await asyncio.sleep(1)
                res = browser_controller.get_page_text(max_chars=_MAX_PAGE_CHARS)
                if res.get("success"):
                    return res["text"], url, None
            except Exception:
                pass  # fall through to httpx

    # httpx fallback.
    if not url:
        return "", "", "No URL provided and no active browser tab."

    from jarvis.tools.local.content_reader import (
        _extract_readable,
        _httpx_fetch,
    )

    fetched = await asyncio.to_thread(_httpx_fetch, url, 20.0)
    if not fetched.get("success"):
        return "", url, fetched.get("error", "Fetch failed")
    text = _extract_readable(fetched["html"], max_chars=_MAX_PAGE_CHARS)
    return text, fetched.get("url", url), None


# ---------------------------------------------------------------------------
# Chunk-by-chunk translation
# ---------------------------------------------------------------------------

def _split_chunks(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into chunks, preferring line boundaries."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        # Try to break at a newline near chunk_size.
        cut = text.rfind("\n", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = chunk_size  # no good newline, hard cut
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def _translate_chunk(
    ollama_client: Any, text: str, target_language: str,
) -> str:
    """Translate a single text chunk via LLM."""
    prompt = (
        f"Translate the following text into {target_language}. "
        "Preserve formatting and paragraph structure. "
        "Output ONLY the translation, no commentary.\n\n"
        f"{text}"
    )
    messages = [{"role": "user", "content": prompt}]
    parts: list[str] = []
    async for chunk in ollama_client.stream_chat(messages):
        if chunk.content:
            parts.append(chunk.content)
        if chunk.done:
            break
    return "".join(parts).strip()


async def _translate_full(
    ollama_client: Any, text: str, target_language: str,
) -> str:
    """Translate text, chunking if necessary."""
    chunks = _split_chunks(text)
    if len(chunks) == 1:
        return await _translate_chunk(ollama_client, chunks[0], target_language)

    translated_parts: list[str] = []
    for i, chunk in enumerate(chunks):
        log.debug("Translating chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
        translated = await _translate_chunk(ollama_client, chunk, target_language)
        translated_parts.append(translated)
    return "\n\n".join(translated_parts)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class TranslatePageTool:
    """Translate a web page into a target language via LLM."""

    name: str = "translate_page"
    description: str = (
        "Translates a web page into a specified language using the LLM. "
        "Reads the page text, translates chunk-by-chunk for long pages, "
        "and returns the translated text. Optionally saves to file. "
        "(RU: переведи страницу, переведи на русский, переведи на английский / "
        "UK: переклади сторінку, переклади на українську, переклади на англійську)"
    )
    args_schema = TranslatePageArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^translate\s+(?:this\s+)?page$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^translate\s+(?:this\s+)?page\s+to\s+(\w+)$",
            priority=455,
            args=lambda m: {"target_language": m.group(1)},
        ),
        VoicePattern(
            regex=r"^translate\s+to\s+(\w+)$",
            priority=460,
            args=lambda m: {"target_language": m.group(1)},
        ),
        VoicePattern(
            regex=r"^what\s+does\s+this\s+page\s+say$",
            priority=460,
            args=lambda m: {"target_language": "English"},
        ),
        # RU
        VoicePattern(
            regex=r"^переведи\s+страницу$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^переведи\s+на\s+русский$",
            priority=460,
            args=lambda m: {"target_language": "Russian"},
        ),
        VoicePattern(
            regex=r"^переведи\s+на\s+английский$",
            priority=460,
            args=lambda m: {"target_language": "English"},
        ),
        VoicePattern(
            regex=r"^о\s+чём\s+(?:эта\s+)?страница\s+на\s+английском$",
            priority=460,
            args=lambda m: {"target_language": "Russian"},
        ),
        # UK
        VoicePattern(
            regex=r"^переклади\s+сторінку$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^переклади\s+на\s+українську$",
            priority=460,
            args=lambda m: {"target_language": "Ukrainian"},
        ),
        VoicePattern(
            regex=r"^переклади\s+на\s+англійську$",
            priority=460,
            args=lambda m: {"target_language": "English"},
        ),
        VoicePattern(
            regex=r"^про\s+що\s+(?:ця\s+)?сторінка$",
            priority=460,
            args=lambda m: {"target_language": "Ukrainian"},
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

    async def execute(self, args: TranslatePageArgs) -> ToolResult:
        if not self._ollama:
            return ToolResult(
                success=False,
                error="Page translation requires an LLM client, but none is available.",
            )

        target = _normalize_language(args.target_language)

        # Fetch page text.
        text, page_url, err = await _get_page_text(
            args.url, self._browser_controller,
        )
        if err:
            return ToolResult(success=False, error=err)
        if not text.strip():
            return ToolResult(success=False, error="Page appears empty — no text to translate.")

        # Translate.
        try:
            translated = await _translate_full(self._ollama, text, target)
        except Exception as exc:
            return ToolResult(success=False, error=f"Translation failed: {exc}")

        if not translated:
            return ToolResult(success=False, error="LLM returned empty translation.")

        # Optionally save to file.
        saved_path = ""
        if args.save_to_file:
            try:
                from jarvis.paths import data_path

                save_dir = data_path("translations")
                save_dir.mkdir(parents=True, exist_ok=True)
                safe_name = "".join(
                    c if c.isalnum() or c in " -_" else "_"
                    for c in (page_url or "page")[:50]
                )
                out_path = save_dir / f"{safe_name}_{target}.txt"
                out_path.write_text(translated, encoding="utf-8")
                saved_path = str(out_path)
            except Exception:
                log.debug("Could not save translation", exc_info=True)

        # Trim output for TTS (keep full for display).
        preview = translated[:2000] + ("…" if len(translated) > 2000 else "")
        suffix = f"\nSaved to {saved_path}" if saved_path else ""
        return ToolResult(
            success=True,
            output=f"Translation to {target} ({len(translated)} chars):\n{preview}{suffix}",
        )
