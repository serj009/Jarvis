"""Read text visible on screen using OCR (Tesseract) — no GPU needed.

A lightweight complement to ``see_screen``:

- **see_screen** sends the screenshot to a vision LLM (Ollama) and
  returns a *description* of the screen.  Needs a pulled model and a
  GPU warm-up cycle.
- **read_screen_text** runs Tesseract OCR locally, returning the raw
  *text* visible on screen.  No Ollama, no GPU, works even in game mode
  when the LLM is evicted from VRAM.

Best for:
  - Reading error messages, dialog boxes, status bars.
  - Extracting text from fullscreen games (where llava struggles with
    small font rendering).
  - Accessibility and automation on text-heavy UIs.

Graceful fallback: when Tesseract is not installed the tool returns a
helpful install hint rather than crashing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.vision.ocr import ScreenOCR

log = logging.getLogger(__name__)


class ReadScreenTextArgs(BaseModel):
    """No arguments — captures and OCRs the current screen."""


class ReadScreenTextTool:
    """Read text visible on screen using OCR (no GPU needed).

    Faster and more accurate for text than vision models.
    Works offline, no Ollama required.
    """

    name: str = "read_screen_text"
    description: str = (
        "Reads all text currently visible on screen using OCR. "
        "Faster and more accurate for text than a vision model, and "
        "requires no GPU or Ollama. Use when the user wants to know "
        "what text is displayed (error messages, dialog content, "
        "subtitles, UI labels). "
        "(RU: прочитай текст на экране / UK: прочитай текст на екрані)"
    )
    args_schema: type[BaseModel] = ReadScreenTextArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=(
                r"^read\s+(?:the\s+)?(?:text|words)\s+"
                r"(?:on\s+)?(?:the\s+)?(?:screen|display|monitor)$"
            ),
            priority=375,
        ),
        VoicePattern(
            regex=r"^what\s+(?:does\s+it|does\s+that|do\s+the\s+words?)\s+say$",
            priority=375,
        ),
        VoicePattern(
            regex=(
                r"^(?:extract|grab|get)\s+(?:the\s+)?text\s+"
                r"(?:from\s+|on\s+)?(?:the\s+)?(?:screen|display|monitor)$"
            ),
            priority=375,
        ),
        # RU
        VoicePattern(
            regex=r"^(?:прочитай|прочти)\s+(?:текст\s+)?(?:на\s+)?экран[еу]?$",
            priority=375,
        ),
        VoicePattern(
            regex=(
                r"^что\s+(?:там\s+)?(?:написано|за\s+текст)"
                r"(?:\s+на\s+экране)?$"
            ),
            priority=375,
        ),
        VoicePattern(
            regex=(
                r"^(?:покажи|скажи|назови)\s+(?:мне\s+)?текст\s+"
                r"(?:на\s+|с\s+)?экран[еау]?$"
            ),
            priority=375,
        ),
        # UK
        VoicePattern(
            regex=r"^(?:прочитай|прочти)\s+(?:текст\s+)?(?:на\s+)?екран[іу]?$",
            priority=375,
        ),
        VoicePattern(
            regex=(
                r"^що\s+(?:там\s+)?(?:написано|за\s+текст)"
                r"(?:\s+на\s+екрані)?$"
            ),
            priority=375,
        ),
        VoicePattern(
            regex=(
                r"^(?:покажи|скажи|назви)\s+(?:мені\s+)?текст\s+"
                r"(?:на\s+|з\s+)?екран[іу]?$"
            ),
            priority=375,
        ),
    )

    def __init__(self, *, ocr: ScreenOCR | None = None) -> None:
        self._ocr = ocr or ScreenOCR()

    async def execute(self, args: ReadScreenTextArgs) -> ToolResult:
        if not self._ocr.is_available():
            return ToolResult(
                success=False,
                error=(
                    "Tesseract OCR is not installed, sir. "
                    "Install it from https://github.com/tesseract-ocr/tesseract "
                    "and then pip install pytesseract."
                ),
            )

        try:
            image = await asyncio.to_thread(self._capture_screen)
        except Exception as exc:
            log.exception("read_screen_text: screen capture failed")
            return ToolResult(
                success=False,
                error=f"I couldn't capture the screen, sir: {exc}",
            )

        text = await asyncio.to_thread(self._ocr.extract_text, image)

        if not text.strip():
            return ToolResult(
                success=True,
                output="I don't see any readable text on the screen, sir.",
            )

        # Security Layer 3: OCR'd screen text is untrusted external content
        # (the screen may show an attacker-controlled page). Strip
        # prompt-injection payloads before it enters the LLM context.
        from jarvis.security.output_sanitizer import clean_external_text

        text = clean_external_text(text, source="read_screen_text:ocr")
        return ToolResult(success=True, output=text)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _capture_screen():  # noqa: ANN205
        """Grab the primary display. Runs in a thread."""
        import pyautogui  # lazy

        image = pyautogui.screenshot()
        if image is None:
            raise RuntimeError("pyautogui.screenshot returned no image")
        return image
