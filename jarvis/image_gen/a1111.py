"""Automatic1111 / Stable Diffusion WebUI image generation provider.

HTTP API:
  POST /sdapi/v1/txt2img    — generate an image
  GET  /sdapi/v1/sd-models   — list available checkpoints

Same freedom as ComfyUI — any model the user has installed.
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


class A1111Provider(ImageProvider):
    """Automatic1111 / Forge local image generation."""

    def __init__(self, config: ImageGenConfig) -> None:
        self._endpoint = config.local_endpoint.rstrip("/")
        self._model = config.local_model
        self._save_dir = config.save_directory
        self._nsfw_filter = config.nsfw_filter

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

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "batch_size": 1,
            "n_iter": 1,
        }
        if self._model:
            payload["override_settings"] = {
                "sd_model_checkpoint": self._model,
            }

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self._endpoint}/sdapi/v1/txt2img",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("images", [])
        if not images:
            raise RuntimeError("A1111 returned no images")

        img_bytes = base64.b64decode(images[0])

        save_dir = Path(self._save_dir) if self._save_dir else (
            Path.home() / "Pictures" / "Jarvis"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = save_dir / f"jarvis_gen_{stamp}.png"
        out_path.write_bytes(img_bytes)
        log.info("A1111: saved image to %s", out_path)
        return out_path

    async def list_models(self) -> list[str]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._endpoint}/sdapi/v1/sd-models",
                )
                resp.raise_for_status()
                models = resp.json()
        except Exception as exc:
            log.warning("A1111: failed to list models: %s", exc)
            return []

        return [m.get("title", m.get("model_name", "")) for m in models]

    def is_available(self) -> bool:
        try:
            import httpx
            resp = httpx.get(
                f"{self._endpoint}/sdapi/v1/sd-models", timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
