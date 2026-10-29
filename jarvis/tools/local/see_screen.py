"""Vision tool — capture the screen, ship it to a multimodal Ollama model,
speak the description back.

Pipeline
--------
1. Grab the primary display via pyautogui (same library as ScreenshotTool;
   late-imported because it pulls in mouse/keyboard hooks).
2. Resize to the configured long-edge cap (default 1280 px) with Pillow.
   Vision models do their own internal downscale; this cap just keeps the
   HTTP payload small and the prompt-eval time bounded.
3. Encode PNG → base64.
4. POST to Ollama's /api/chat with the vision model and the user's
   question (defaults to a generic "describe the screen" prompt).
5. Save the captured PNG to the user's screenshots folder so they have a
   reference for what Jarvis was looking at, then return the model's
   response as the spoken output.

Why a separate tool, not just adding images to the main LLM
----------------------------------------------------------
Vision-capable Ollama models (llava, moondream, minicpm-v, qwen2.5-vl)
are different from the conversational text model (`llm.model`). Letting
the main router pass images to qwen2.5:7b-instruct fails opaquely inside
Ollama. Exposing a dedicated `see_screen` tool with its own model
(VisionConfig.model) makes the swap explicit and gracefully degrades
when no vision model is pulled."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from jarvis.platform import windows as winplat
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis.core.game_mode import GameMode
    from jarvis.vision.ocr import ScreenOCR


_DEFAULT_PROMPT = (
    "You are Jarvis, an AI assistant. Describe what is on the user's "
    "screen briefly, in one or two sentences. Mention the active "
    "application, the visible content, and anything noteworthy "
    "(errors, dialogs, notifications). Address the user as 'sir'."
)


class SeeScreenArgs(BaseModel):
    question: str = Field(
        default="",
        description=(
            "Optional follow-up question about what's on screen. "
            "Examples: 'what does the error message say', 'is there a "
            "notification', 'summarize this article'. Leave blank for a "
            "generic description."
        ),
    )


class SeeScreenTool:
    name: str = "see_screen"
    description: str = (
        "Captures the user's screen and describes what's on it using a "
        "vision-capable LLM. Use this whenever the user asks you to see, "
        "look at, read, or describe their screen, or to identify what's "
        "visible (errors, dialogs, content of an article, what app is "
        "open, etc.). Optionally accepts a 'question' argument for "
        "follow-up queries about the screen contents. "
        "(RU: что на экране, посмотри / UK: що на екрані, подивись)"
    )
    args_schema: type[BaseModel] = SeeScreenArgs
    requires_confirmation: bool = False
    # 380-410: the trailing "screen|display|monitor" anchors the tool so
    # the generic verbs (open, close, lock, read) keep their own
    # patterns. Kept ahead of the notes patterns (420+) so "read my
    # screen" can never be read as a note title.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:look\s+at|see|read|describe)\s+(?:my\s+|the\s+)?"
            r"(?:screen|display|monitor)$",
            priority=380,
        ),
        # The suffix is required so "what's" alone never fires a tool.
        VoicePattern(
            regex=r"^what(?:'s|\s+is)\s+on\s+(?:my\s+|the\s+)?"
            r"(?:screen|display|monitor)$",
            priority=390,
        ),
        # "see" here is the visual verb, not the tool name.
        VoicePattern(
            regex=r"^what\s+(?:do|can)\s+you\s+see"
            r"(?:\s+on\s+(?:my\s+|the\s+)?(?:screen|display|monitor))?$",
            priority=400,
        ),
        # Subsumed by priority 380 after filler-stripping turns
        # "can you see my screen" into "see my screen"; kept because
        # removing it would be a behaviour change to prove rather than
        # assume, and it costs one regex.
        VoicePattern(
            regex=r"^(?:can\s+you\s+)?see\s+my\s+screen$", priority=410
        ),
        # RU: "посмотри на экран", "опиши экран", "что на экране"
        VoicePattern(
            regex=r"^(?:посмотри|опиши|что)\s+(?:на\s+)?(?:мо[ёе]м\s+)?экран[еу]?\??$",
            priority=380,
        ),
        # UK: "подивись на екран", "опиши екран", "що на екрані"
        VoicePattern(
            regex=r"^(?:подивись|опиши|що)\s+(?:на\s+)?(?:моєму\s+)?екран[іу]?\??$",
            priority=380,
        ),
    )

    def __init__(
        self,
        *,
        ollama_client,
        vision_config,
        game_mode: GameMode | None = None,
        ocr: ScreenOCR | None = None,
    ) -> None:
        # `ollama_client` is the live OllamaClient instance. We use its
        # vision_chat() method (model name is per-call, not bound to the
        # client's text-model config).
        self._ollama = ollama_client
        self._cfg = vision_config
        # Game mode: when active, Ollama is evicted from VRAM.  Use
        # on_demand_vision_query() for temp load → answer → unload.
        self._game_mode = game_mode
        # Optional OCR module — enriches the vision prompt with text.
        self._ocr = ocr

    async def execute(self, args: SeeScreenArgs) -> ToolResult:
        model = (getattr(self._cfg, "model", "") or "").strip()
        if not model:
            return ToolResult(
                success=False,
                error=(
                    "No vision model configured, sir. Please set one "
                    "under Settings → Models → Vision model."
                ),
            )

        try:
            image_b64, saved_path = await asyncio.to_thread(
                self._capture_and_encode,
                int(getattr(self._cfg, "max_image_dim", 1280)),
            )
        except Exception as exc:
            log.exception("see_screen: screen capture failed")
            return ToolResult(
                success=False,
                error=f"I couldn't capture the screen, sir: {exc}",
            )

        prompt = self._build_prompt(args.question, image_b64=image_b64)

        # Game mode: Ollama is evicted from VRAM — use temp load cycle
        if self._game_mode is not None and self._game_mode.is_active:
            try:
                description = await self._game_mode.on_demand_vision_query(
                    image_b64=image_b64,
                    question=prompt,
                    model=model,
                )
            except Exception as exc:
                log.warning("see_screen: game-mode vision query failed: %s", exc)
                return ToolResult(
                    success=False,
                    error="I couldn't reach the vision model in game mode, sir.",
                )
            if not description:
                return ToolResult(success=True, output="I'm not sure what I'm looking at, sir.")
            log.info("see_screen: game-mode snapshot saved to %s", saved_path)
            return ToolResult(success=True, output=description)
        else:
            try:
                description = await self._ollama.vision_chat(
                    model=model,
                    prompt=prompt,
                    image_b64=image_b64,
                    max_tokens=int(getattr(self._cfg, "max_tokens", 512)),
                    temperature=float(getattr(self._cfg, "temperature", 0.2)),
                )
            except Exception as exc:
                log.warning("see_screen: vision_chat failed: %s", exc)
                msg = str(exc)
                if "not found" in msg.lower():
                    return ToolResult(
                        success=False,
                        error=(
                            f"The vision model {model!r} isn't pulled, sir. "
                            f"Run: ollama pull {model}"
                        ),
                    )
                return ToolResult(
                    success=False,
                    error="I couldn't reach the vision model, sir.",
                )

            if not description:
                return ToolResult(
                    success=True,
                    output="I'm not sure what I'm looking at, sir.",
                )
            log.info("see_screen: snapshot saved to %s", saved_path)
            return ToolResult(success=True, output=description)

    # -- internal --

    def _build_prompt(
        self, question: str, *, image_b64: str = "",
    ) -> str:
        """Build the vision-model prompt, enriched with context when
        available.

        Context additions (all optional, all degrade gracefully):

        1. **Game name** — when Game Mode is active and a game has been
           detected, tells the model which game the user is playing so
           it can give game-aware answers.
        2. **OCR text** — when Tesseract is installed, feeds the first
           ~500 characters of screen text into the prompt.  The vision
           model (llava:7b) is weak at reading small UI text; OCR text
           compensates by giving it a textual transcript to reason over.
        """
        base_question = (question or "").strip()

        context_parts: list[str] = []

        # 1. Game context
        if self._game_mode and self._game_mode.is_active:
            current = self._game_mode.current_game
            if current and current.title:
                context_parts.append(
                    f"The user is currently playing {current.title}."
                )

        # 2. OCR text (helps the vision model read small UI text)
        if self._ocr and self._ocr.is_available():
            try:
                import io as _io

                from PIL import Image as _PILImage  # lazy
                raw = base64.b64decode(image_b64)
                img = _PILImage.open(_io.BytesIO(raw))
                ocr_text = self._ocr.extract_text(img)
                if ocr_text.strip():
                    context_parts.append(
                        f"Text visible on screen (OCR): {ocr_text[:500]}"
                    )
            except Exception:
                log.debug("see_screen: OCR augmentation failed", exc_info=True)

        if not base_question:
            base_prompt = _DEFAULT_PROMPT
        else:
            base_prompt = (
            f"{_DEFAULT_PROMPT}\n\nUser's specific question: {base_question}\n"
            "Answer the question directly using only what you see in the "
            "image. If the answer isn't visible, say so."
            )

        if context_parts:
            return "\n".join(context_parts) + "\n\n" + base_prompt
        return base_prompt

    @staticmethod
    def _capture_and_encode(max_dim: int) -> tuple[str, str]:
        """Capture the primary display, downscale long edge to `max_dim`,
        save a copy to the user's screenshots dir, and return
        (base64_png, saved_path).

        Runs on a worker thread so the audio loop never sees the
        synchronous pyautogui call.
        """
        # Late imports: pyautogui pulls in mouse/keyboard hooks at import
        # time; Pillow loads native codecs lazily. Both are cold-import
        # candidates if see_screen is never used.
        import pyautogui
        from PIL import Image

        image: Image.Image = pyautogui.screenshot()
        if image is None:
            raise RuntimeError("pyautogui.screenshot returned no image")

        # Long-edge resize. LANCZOS is the right choice for downscale —
        # NEAREST mangles UI text, BILINEAR is acceptable but softer.
        width, height = image.size
        long_edge = max(width, height)
        if long_edge > max_dim and long_edge > 0:
            scale = max_dim / float(long_edge)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        target = winplat.screenshots_dir() / (
            datetime.now().strftime("jarvis_see_%Y%m%d_%H%M%S.png")
        )
        # Save a copy for the user (so they can audit what Jarvis saw),
        # then re-encode into memory for the API call. Saving via PNG
        # twice is the cleanest path — PIL's save() flow handles the
        # format-specific compression and metadata.
        image.save(target, format="PNG")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii"), str(target)
