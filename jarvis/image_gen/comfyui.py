"""ComfyUI image generation provider.

HTTP API:
  POST /prompt         — queue a workflow
  GET  /history/{id}   — poll for completion
  GET  /view           — download result image
  GET  /object_info    — enumerate available models

ComfyUI uses workflow JSON (a node graph). We build a minimal txt2img
workflow that chains: CheckpointLoaderSimple → CLIPTextEncode (pos/neg)
→ KSampler → VAEDecode → SaveImage.

Supports ANY model the user has installed (SDXL, SD1.5, Flux, NSFW
models, etc.) — no content filtering from our side unless the user
explicitly enables nsfw_filter.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from jarvis.core.config import ImageGenConfig

from jarvis.image_gen.provider import ImageProvider

log = logging.getLogger(__name__)


def _build_txt2img_workflow(
    prompt: str,
    negative_prompt: str,
    model: str,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: int = -1,
) -> dict[str, Any]:
    """Build a minimal ComfyUI txt2img workflow as a node graph dict.

    Seed -1 means random; ComfyUI resolves it server-side.
    """
    if seed < 0:
        import random
        seed = random.randint(0, 2**63)

    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or "",
                "clip": ["4", 1],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "jarvis"},
        },
    }


class ComfyUIProvider(ImageProvider):
    """ComfyUI local image generation via its HTTP API."""

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
        workflow = _build_txt2img_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            model=self._model,
            width=width,
            height=height,
            steps=steps,
            cfg_scale=cfg_scale,
        )

        client_id = str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": client_id}

        async with httpx.AsyncClient(timeout=300) as client:
            # Queue the workflow
            resp = await client.post(
                f"{self._endpoint}/prompt", json=payload,
            )
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]
            log.info("ComfyUI: queued prompt %s", prompt_id)

            # Poll /history until done
            for _ in range(600):  # 10 min max
                await asyncio.sleep(1.0)
                hist = await client.get(
                    f"{self._endpoint}/history/{prompt_id}",
                )
                hist.raise_for_status()
                data = hist.json()
                if prompt_id in data:
                    break
            else:
                raise TimeoutError("ComfyUI generation timed out after 600s")

            # Extract output image filename from SaveImage node
            outputs = data[prompt_id].get("outputs", {})
            images: list[dict] = []
            for node_output in outputs.values():
                images.extend(node_output.get("images", []))
            if not images:
                raise RuntimeError("ComfyUI returned no images")

            img_info = images[0]
            filename = img_info["filename"]
            subfolder = img_info.get("subfolder", "")
            img_type = img_info.get("type", "output")

            # Download the image via /view
            params = {
                "filename": filename,
                "subfolder": subfolder,
                "type": img_type,
            }
            img_resp = await client.get(
                f"{self._endpoint}/view", params=params,
            )
            img_resp.raise_for_status()

        # Save locally
        save_dir = Path(self._save_dir) if self._save_dir else (
            Path.home() / "Pictures" / "Jarvis"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = save_dir / f"jarvis_gen_{stamp}.png"
        out_path.write_bytes(img_resp.content)
        log.info("ComfyUI: saved image to %s", out_path)
        return out_path

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._endpoint}/object_info")
                resp.raise_for_status()
                info = resp.json()
        except Exception as exc:
            log.warning("ComfyUI: failed to list models: %s", exc)
            return []

        try:
            ckpt_node = info["CheckpointLoaderSimple"]["input"]["required"]
            return list(ckpt_node["ckpt_name"][0])
        except (KeyError, IndexError, TypeError):
            return []

    def is_available(self) -> bool:
        try:
            resp = httpx.get(f"{self._endpoint}/system_stats", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
