"""Voice tool for image generation — ``generate_image``.

Routes to whatever provider is configured in ImageGenConfig. Supports
voice patterns in English, Russian, and Ukrainian.

When ``auto_vram_swap`` is enabled and a local provider is active,
evicts the LLM from VRAM before generating and restores it after.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from jarvis.image_gen.provider import ImageProvider
from jarvis.tools.registry import ToolResult, VoicePattern

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

log = logging.getLogger(__name__)

_LOCAL_PROVIDERS = frozenset({"comfyui", "a1111"})


class ImageGenArgs(BaseModel):
    prompt: str = Field(
        description=(
            "Text description of the image to generate. Be specific: "
            "include subject, style, lighting, mood. "
            "Examples: 'a cyberpunk cityscape at sunset, neon lights', "
            "'watercolor painting of a cat sleeping on a windowsill'."
        ),
    )
    negative_prompt: str = Field(
        default="",
        description="What to avoid in the image (e.g. 'blurry, low quality').",
    )


class ImageGenTool:
    name: str = "generate_image"
    description: str = (
        "Generates an image from a text description using the configured "
        "image generation provider (ComfyUI, A1111, DALL-E, Stability AI, "
        "or Replicate). Provide a detailed 'prompt' describing the desired "
        "image. Optionally provide a 'negative_prompt' for things to avoid. "
        "(RU: нарисуй/сгенерируй картинку / UK: намалюй/згенеруй картинку)"
    )
    args_schema: type[BaseModel] = ImageGenArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "generate an image of ...", "create a picture of ..."
        VoicePattern(
            regex=(
                r"^(?:generate|create|draw|make)\s+(?:an?\s+)?"
                r"(?:image|picture|photo)\s+(?:of\s+)?(.+)$"
            ),
            priority=200,
        ),
        # EN: "draw ..."
        VoicePattern(
            regex=r"^draw\s+(?:me\s+)?(.+)$",
            priority=300,
        ),
        # RU: "нарисуй ...", "сгенерируй картинку ...", "создай картинку ..."
        VoicePattern(
            regex=(
                r"^(?:нарисуй|сгенерируй|создай)\s+"
                r"(?:картинк[уи]\s+)?(.+)$"
            ),
            priority=200,
        ),
        # UK: "намалюй ...", "згенеруй картинку ...", "створи картинку ..."
        VoicePattern(
            regex=(
                r"^(?:намалюй|згенеруй|створи)\s+"
                r"(?:картинк[уи]\s+)?(.+)$"
            ),
            priority=200,
        ),
    )

    def __init__(
        self,
        *,
        image_gen_config: ImageGenConfig,
        ollama_client: object | None = None,
        model_provider: object | None = None,
    ) -> None:
        self._cfg = image_gen_config
        self._ollama = ollama_client
        self._model_provider = model_provider

    async def execute(self, args: ImageGenArgs) -> ToolResult:
        if self._cfg.provider == "disabled" or not self._cfg.provider:
            return ToolResult(
                success=False,
                error=(
                    "Image generation is disabled, sir. "
                    "Please configure a provider under "
                    "Settings → Image Gen."
                ),
            )

        try:
            provider = ImageProvider.from_config(self._cfg)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to initialize image provider: {exc}",
            )

        # --- Prompt enhancement ---
        prompt = args.prompt
        negative = args.negative_prompt
        # Track whether we're using an isolated image model
        _isolated = self._cfg.image_model_provider != "main"

        if self._cfg.enhance_mode != "off":
            try:
                from jarvis.image_gen.prompt_enhancer import (
                    PromptEnhancer,
                    detect_style_from_text,
                )

                detected_style, cleaned = detect_style_from_text(prompt)
                style = detected_style or self._cfg.default_style

                if _isolated and self._cfg.enhance_mode == "llm":
                    # --- ISOLATED mode ---
                    # Build a dedicated provider for image prompt work.
                    # Main LLM never sees the prompt — full isolation.
                    from jarvis.llm.model_provider import ModelProvider

                    image_provider = ModelProvider.from_image_config(
                        self._cfg, self._ollama,
                    )

                    # VRAM swap step 1: evict main LLM to free VRAM
                    # for the image model (only when both are Ollama-local).
                    if (
                        self._ollama is not None
                        and self._cfg.auto_vram_swap
                        and self._cfg.image_model_provider == "ollama"
                    ):
                        try:
                            await self._ollama.unload()
                        except Exception:
                            log.warning(
                                "Failed to evict main LLM before image model, "
                                "proceeding anyway",
                                exc_info=True,
                            )

                    enhancer = PromptEnhancer(
                        mode="llm",
                        provider=image_provider,
                    )
                else:
                    # DEFAULT mode: main LLM handles enhancement
                    enhancer = PromptEnhancer(
                        mode=self._cfg.enhance_mode,
                        provider=self._model_provider,
                    )

                enhanced = await enhancer.enhance(cleaned, style=style)
                prompt = enhanced["prompt"]
                negative = negative or enhanced["negative_prompt"]
            except Exception:
                log.warning("Prompt enhancement failed, using raw prompt", exc_info=True)

        # --- VRAM swap for local providers ---
        # If we used an isolated Ollama image model, it's still loaded.
        # Unload it now before SD generation (or before restoring main LLM).
        if _isolated and self._cfg.image_model_provider == "ollama":
            try:
                # Send keep_alive=0 to the image model to evict it.
                # We use the ollama client's unload (which talks to the daemon).
                if self._ollama is not None and self._cfg.auto_vram_swap:
                    await self._ollama.unload()
            except Exception:
                log.warning(
                    "Failed to evict image model after prompt enhancement",
                    exc_info=True,
                )

        # --- VRAM swap for local providers (SD generation) ---
        is_local = self._cfg.provider.lower() in _LOCAL_PROVIDERS
        if is_local and self._cfg.auto_vram_swap:
            from jarvis.image_gen.vram_helper import with_vram_for_sd

            async def _gen() -> Path:
                return await provider.generate(
                    prompt=prompt,
                    negative_prompt=negative,
                    width=self._cfg.width,
                    height=self._cfg.height,
                    steps=self._cfg.steps,
                    cfg_scale=self._cfg.cfg_scale,
                )

            try:
                result_path = await with_vram_for_sd(
                    vram_manager=None,
                    ollama_client=self._ollama,
                    func=_gen,
                )
            except Exception as exc:
                log.exception("Image generation failed (local + VRAM swap)")
                return ToolResult(
                    success=False,
                    error=f"Image generation failed, sir: {exc}",
                )
        else:
            try:
                result_path = await provider.generate(
                    prompt=prompt,
                    negative_prompt=negative,
                    width=self._cfg.width,
                    height=self._cfg.height,
                    steps=self._cfg.steps,
                    cfg_scale=self._cfg.cfg_scale,
                )
            except Exception as exc:
                log.exception("Image generation failed")
                return ToolResult(
                    success=False,
                    error=f"Image generation failed, sir: {exc}",
                )

        # Try to open the image for the user
        try:
            os.startfile(str(result_path))  # type: ignore[attr-defined]
        except Exception:
            log.debug("Could not auto-open image: %s", result_path)

        return ToolResult(
            success=True,
            output=(
                f"Картинка готова, sir! Saved to {result_path.name}"
            ),
        )
