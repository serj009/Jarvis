"""Report CPU, RAM, and GPU utilisation as a spoken summary (roadmap T1.3).

Enhanced from the original CPU/RAM-only version to include:
- GPU memory usage and temperature via nvidia-smi (uses VRAMManager snapshot)
- VRAM consumer breakdown (which models are loaded)
- i18n support (ru/uk/en) via phrases module
- Voice summary suitable for TTS
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from jarvis.core.phrases import reply_language
from jarvis.tools.registry import EmptyArgs, ToolResult

if TYPE_CHECKING:
    from jarvis.core.vram_manager import VRAMManager


# ---------------------------------------------------------------------------
# i18n templates
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "en": {
        "cpu_ram": "CPU at {cpu:.0f} percent, memory at {mem:.0f} percent.",
        "gpu": "GPU memory: {used} of {total} megabytes used, {free} free.",
        "gpu_temp": "GPU temperature: {temp} degrees.",
        "gpu_none": "GPU information is not available.",
        "consumers": "Loaded models: {models}.",
        "no_consumers": "No models currently loaded on GPU.",
        "full": "{cpu_ram} {gpu_info} {consumer_info} {sir}",
        "sir": "That's your status, sir.",
    },
    "ru": {
        "cpu_ram": "Процессор загружен на {cpu:.0f} процентов, оперативная память на {mem:.0f} процентов.",
        "gpu": "Видеопамять: {used} из {total} мегабайт занято, {free} свободно.",
        "gpu_temp": "Температура видеокарты: {temp} градусов.",
        "gpu_none": "Информация о видеокарте недоступна.",
        "consumers": "Загруженные модели: {models}.",
        "no_consumers": "Моделей на видеокарте сейчас нет.",
        "full": "{cpu_ram} {gpu_info} {consumer_info} {sir}",
        "sir": "Вот ваш статус, сэр.",
    },
    "uk": {
        "cpu_ram": "Процесор завантажений на {cpu:.0f} відсотків, оперативна пам'ять на {mem:.0f} відсотків.",
        "gpu": "Відеопам'ять: {used} із {total} мегабайт зайнято, {free} вільно.",
        "gpu_temp": "Температура відеокарти: {temp} градусів.",
        "gpu_none": "Інформація про відеокарту недоступна.",
        "consumers": "Завантажені моделі: {models}.",
        "no_consumers": "Моделей на відеокарті зараз немає.",
        "full": "{cpu_ram} {gpu_info} {consumer_info} {sir}",
        "sir": "Ось ваш статус, сер.",
    },
}


class SystemStatsTool:
    # Long action-specific name on purpose: when this tool was called
    # `system_stats`, a small LLM treated it as a generic "tell me about
    # the system" probe and fired it for ambiguous transcriptions
    # ("jot", "in chat", "capital of france"). The verbose name signals
    # to the model that this is a narrow numeric tool, not a fallback.
    name: str = "report_cpu_and_memory_percentages"
    description: str = (
        "Reports the current CPU usage percentage, memory usage "
        "percentage, and GPU/VRAM status including loaded models. "
        "Only use this when the user explicitly asks for "
        "CPU, memory, RAM, GPU, VRAM, or system performance numbers. Never use "
        "this for general questions, greetings, conversations, or "
        "factual queries."
    )
    args_schema: type[BaseModel] = EmptyArgs
    requires_confirmation: bool = False

    def __init__(self, *, vram_manager: VRAMManager | None = None) -> None:
        self._vram = vram_manager

    async def execute(self, args: EmptyArgs) -> ToolResult:
        lang = reply_language()
        t = _TEMPLATES.get(lang, _TEMPLATES["en"])

        # --- CPU / RAM (unchanged logic) ---
        def _sample() -> tuple[float, float]:
            import psutil

            cpu = psutil.cpu_percent(interval=0.2)
            mem = psutil.virtual_memory().percent
            return cpu, mem

        try:
            cpu, mem = await asyncio.to_thread(_sample)
        except Exception as e:
            return ToolResult(success=False, error=f"could not read stats: {e}")

        cpu_ram = t["cpu_ram"].format(cpu=cpu, mem=mem)

        # --- GPU / VRAM ---
        gpu_info = t["gpu_none"]
        consumer_info = t["no_consumers"]

        if self._vram is not None:
            snap = await self._vram.refresh_snapshot()
            if snap is not None:
                gpu_info = t["gpu"].format(
                    used=snap.used_mb, total=snap.total_mb, free=snap.free_mb
                )
                if snap.temperature_c is not None:
                    gpu_info += " " + t["gpu_temp"].format(temp=snap.temperature_c)

            # Consumer breakdown
            status = self._vram.status()
            loaded = [
                f"{name} ({info['size_mb']} MB)"
                for name, info in status["consumers"].items()
                if info["loaded"]
            ]
            if loaded:
                consumer_info = t["consumers"].format(models=", ".join(loaded))

        full = t["full"].format(
            cpu_ram=cpu_ram,
            gpu_info=gpu_info,
            consumer_info=consumer_info,
            sir=t["sir"],
        )
        return ToolResult(success=True, output=full)
