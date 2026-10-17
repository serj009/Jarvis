"""Image generation subsystem — multi-provider abstraction.

Providers:
  - ComfyUI (local, any model the user has)
  - Automatic1111 / Forge (local, any model)
  - OpenAI DALL-E (cloud, content-filtered)
  - Stability AI (cloud, moderate filtering)
  - Replicate (cloud, many NSFW models available)

All providers implement the same ``ImageProvider`` ABC. The factory
``ImageProvider.from_config()`` builds the right one from
``ImageGenConfig.provider``.

Prompt enhancement:
  - ``PromptEnhancer`` converts short descriptions into full SD prompts
    with quality tags, style presets, and negative prompts.
"""

from jarvis.image_gen.prompt_enhancer import PromptEnhancer
from jarvis.image_gen.provider import ImageProvider

__all__ = ["ImageProvider", "PromptEnhancer"]
