"""OpenAI DALL-E image generation provider.

API:
  POST /v1/images/generations

Models: dall-e-3, dall-e-2.
Has content policy restrictions — JARVIS warns the user when the prompt
is rejected.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

from jarvis.image_gen.provider import ImageProvider

log = logging.getLogger(__name__)

_SUPPORTED_MODELS = ("dall-e-3", "dall-e-2")


class OpenAIDalleProvider(ImageProvider):
    """OpenAI DALL-E cloud image generation."""

    def __init__(self, config: ImageGenConfig) -> None:
        self._api_key = config.api_key
        self._model = config.api_model or "dall-e-3"
        self._save_dir = config.save_directory

    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        cfg_scale: float = 7.0,
    ) -> Path:
        if not self._api_key:
            raise RuntimeError("OpenAI API key not configured")

        # DALL-E 3 supports 1024x1024, 1792x1024, 1024x1792
        size = self._resolve_size(width, height)

        # DALL-E ignores negative_prompt; append it as instruction
        full_prompt = prompt
        if negative_prompt:
            full_prompt += f"\n\nAvoid: {negative_prompt}"

        payload = {
            "model": self._model,
            "prompt": full_prompt,
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("data", [])
        if not images:
            raise RuntimeError("DALL-E returned no images")

        import base64
        img_bytes = base64.b64decode(images[0]["b64_json"])

        save_dir = Path(self._save_dir) if self._save_dir else (
            Path.home() / "Pictures" / "Jarvis"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = save_dir / f"jarvis_dalle_{stamp}.png"
        out_path.write_bytes(img_bytes)
        log.info("DALL-E: saved image to %s", out_path)
        return out_path

    @staticmethod
    def _resolve_size(width: int, height: int) -> str:
        """Map arbitrary dimensions to DALL-E supported sizes."""
        aspect = width / height if height else 1.0
        if aspect > 1.4:
            return "1792x1024"
        elif aspect < 0.7:
            return "1024x1792"
        return "1024x1024"

    async def list_models(self) -> list[str]:
        return list(_SUPPORTED_MODELS)

    def is_available(self) -> bool:
        return bool(self._api_key)
