"""Replicate image generation provider.

API:
  POST /v1/predictions — create a prediction
  GET  /v1/predictions/{id} — poll for result

Replicate hosts many community models. The user provides a model ID
(e.g. ``stability-ai/sdxl``, ``black-forest-labs/flux-schnell``).
Least restrictive API — many NSFW models available.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

from jarvis.image_gen.provider import ImageProvider

log = logging.getLogger(__name__)

_API_BASE = "https://api.replicate.com"

_EXAMPLE_MODELS = (
    "stability-ai/sdxl:latest",
    "black-forest-labs/flux-schnell",
    "black-forest-labs/flux-dev",
)


class ReplicateProvider(ImageProvider):
    """Replicate cloud image generation (any community model)."""

    def __init__(self, config: ImageGenConfig) -> None:
        self._api_key = config.api_key
        self._model = config.api_model or "stability-ai/sdxl:latest"
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
            raise RuntimeError("Replicate API key not configured")

        # Determine version reference
        model_ref = self._model
        version = None
        if ":" in model_ref:
            parts = model_ref.split(":", 1)
            model_ref = parts[0]
            version = parts[1] if parts[1] != "latest" else None

        input_data = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": steps,
            "guidance_scale": cfg_scale,
        }
        if negative_prompt:
            input_data["negative_prompt"] = negative_prompt

        payload: dict = {"input": input_data}
        if version:
            payload["version"] = version
        else:
            payload["model"] = model_ref

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{_API_BASE}/v1/predictions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            prediction = resp.json()

            # Poll if not completed
            status = prediction.get("status", "")
            poll_url = prediction.get("urls", {}).get("get", "")
            for _ in range(300):
                if status in ("succeeded", "failed", "canceled"):
                    break
                await asyncio.sleep(2.0)
                poll_resp = await client.get(
                    poll_url, headers=headers,
                )
                poll_resp.raise_for_status()
                prediction = poll_resp.json()
                status = prediction.get("status", "")

            if status != "succeeded":
                error = prediction.get("error", "Unknown error")
                raise RuntimeError(f"Replicate prediction failed: {error}")

            output = prediction.get("output")
            if isinstance(output, list) and output:
                image_url = output[0]
            elif isinstance(output, str):
                image_url = output
            else:
                raise RuntimeError("Replicate returned no output images")

            # Download the image
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()

        save_dir = Path(self._save_dir) if self._save_dir else (
            Path.home() / "Pictures" / "Jarvis"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = save_dir / f"jarvis_replicate_{stamp}.png"
        out_path.write_bytes(img_resp.content)
        log.info("Replicate: saved image to %s", out_path)
        return out_path

    async def list_models(self) -> list[str]:
        return list(_EXAMPLE_MODELS)

    def is_available(self) -> bool:
        return bool(self._api_key)
