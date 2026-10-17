"""Stability AI image generation provider.

API:
  POST /v1/generation/{engine_id}/text-to-image

Models: stable-diffusion-xl-1024-v1-0, stable-diffusion-v1-6, etc.
Less restrictive than OpenAI but still has content limits.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

from jarvis.image_gen.provider import ImageProvider

log = logging.getLogger(__name__)

_API_HOST = "https://api.stability.ai"

_SUPPORTED_MODELS = (
    "stable-diffusion-xl-1024-v1-0",
    "stable-diffusion-xl-1024-v0-9",
    "stable-diffusion-v1-6",
)


class StabilityProvider(ImageProvider):
    """Stability AI cloud image generation."""

    def __init__(self, config: ImageGenConfig) -> None:
        self._api_key = config.api_key
        self._model = config.api_model or "stable-diffusion-xl-1024-v1-0"
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
        import httpx

        if not self._api_key:
            raise RuntimeError("Stability AI API key not configured")

        engine_id = self._model

        text_prompts = [{"text": prompt, "weight": 1.0}]
        if negative_prompt:
            text_prompts.append({"text": negative_prompt, "weight": -1.0})

        payload = {
            "text_prompts": text_prompts,
            "cfg_scale": cfg_scale,
            "width": width,
            "height": height,
            "steps": steps,
            "samples": 1,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_API_HOST}/v1/generation/{engine_id}/text-to-image",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        artifacts = data.get("artifacts", [])
        if not artifacts:
            raise RuntimeError("Stability AI returned no images")

        img_bytes = base64.b64decode(artifacts[0]["base64"])

        save_dir = Path(self._save_dir) if self._save_dir else (
            Path.home() / "Pictures" / "Jarvis"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = save_dir / f"jarvis_stability_{stamp}.png"
        out_path.write_bytes(img_bytes)
        log.info("Stability: saved image to %s", out_path)
        return out_path

    async def list_models(self) -> list[str]:
        return list(_SUPPORTED_MODELS)

    def is_available(self) -> bool:
        return bool(self._api_key)
