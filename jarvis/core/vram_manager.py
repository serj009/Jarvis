"""VRAM Manager: GPU memory budget tracking and eviction (roadmap T1.4).

Design notes:

- The RTX 3060 12 GB is shared between Ollama (up to 8 GB), Qwen3-TTS
  (≈2.5 GB), and potentially a vision model.  Without coordination every
  module assumes it has the full GPU and OOM kills happen silently.
- This module keeps a *registry* of named consumers ("ollama", "tts",
  "vision") with their declared VRAM needs.  ``request()`` checks the
  budget before a model loads; ``release()`` marks it freed.
- **Real VRAM measurement**: ``nvidia-smi`` is parsed for ground-truth
  ``gpu_used_mb`` / ``gpu_total_mb``.  The registry is an *overlay*:
  it tracks Jarvis's *own* consumers and does not conflict with external
  GPU usage (games, Stable Diffusion).
- **Eviction**: when ``request()`` cannot fit a new consumer, it evicts
  the lowest-priority loaded consumer (priority is set at registration
  time; lower number = more important = evicted last).  After each
  eviction ``torch.cuda.empty_cache()`` + ``gc.collect()`` are called
  (mandatory per project rules).
- **Gaming mode** ``release_all()``: frees every consumer so the GPU is
  100 % available for games.  Called by LifecycleManager on SLEEPING or
  by a future game-detection hook.
- **Loadable protocol**: VRAMManager implements ``Loadable`` so it can
  participate in the lifecycle (load → read nvidia-smi, unload → release_all).
- Thread safety: all methods are async and expected to run on the audio
  loop.  nvidia-smi is called in ``asyncio.to_thread`` to avoid blocking.

Integration:
    vram = VRAMManager(total_budget_mb=12000)
    vram.register("ollama", size_mb=8000, priority=10)
    vram.register("tts",    size_mb=2500, priority=20)
    ok = await vram.request("tts")  # True if fits, evicts if needed
    await vram.release("tts")       # mandatory empty_cache + gc
"""

from __future__ import annotations

import asyncio
import gc
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.errors import VRAMError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# nvidia-smi helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    """Point-in-time GPU memory reading from nvidia-smi."""

    total_mb: int
    used_mb: int
    free_mb: int
    gpu_name: str = ""
    temperature_c: int | None = None
    utilization_pct: int | None = None


def _parse_nvidia_smi() -> GpuSnapshot | None:
    """Run ``nvidia-smi`` synchronously and return a snapshot, or None."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free,name,temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None
        return GpuSnapshot(
            total_mb=int(parts[0]),
            used_mb=int(parts[1]),
            free_mb=int(parts[2]),
            gpu_name=parts[3] if len(parts) > 3 else "",
            temperature_c=int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None,
            utilization_pct=int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None,
        )
    except Exception:
        log.debug("nvidia-smi parse failed", exc_info=True)
        return None


async def gpu_snapshot() -> GpuSnapshot | None:
    """Async wrapper: runs nvidia-smi in a thread."""
    return await asyncio.to_thread(_parse_nvidia_smi)


# ---------------------------------------------------------------------------
# Consumer registry
# ---------------------------------------------------------------------------

@dataclass
class VRAMConsumer:
    """A registered GPU memory consumer."""

    name: str
    size_mb: int
    priority: int  # lower = more important = evicted last
    is_loaded: bool = False

    # Optional async callbacks invoked on eviction / release.
    on_unload: Any = None  # async def on_unload() -> None


# ---------------------------------------------------------------------------
# VRAMManager
# ---------------------------------------------------------------------------

class VRAMManager:
    """GPU memory budget tracker with eviction support.

    Implements the ``Loadable`` protocol so LifecycleManager can
    orchestrate it alongside audio/LLM modules.
    """

    name: str = "vram_manager"
    is_loaded: bool = False

    def __init__(self, total_budget_mb: int = 12_000) -> None:
        self._total_budget_mb = total_budget_mb
        self._consumers: dict[str, VRAMConsumer] = {}
        self._snapshot: GpuSnapshot | None = None

    # -- Loadable protocol --------------------------------------------------

    async def load(self) -> None:
        if self.is_loaded:
            return
        self._snapshot = await gpu_snapshot()
        if self._snapshot is not None:
            self._total_budget_mb = self._snapshot.total_mb
            log.info(
                "VRAM manager loaded: %s, %d MB total",
                self._snapshot.gpu_name,
                self._total_budget_mb,
            )
        else:
            log.warning(
                "nvidia-smi not available; using configured budget %d MB",
                self._total_budget_mb,
            )
        self.is_loaded = True

    async def unload(self) -> None:
        if not self.is_loaded:
            return
        await self.release_all()
        self.is_loaded = False
        log.info("VRAM manager unloaded")

    # -- registration -------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        size_mb: int,
        priority: int = 50,
        on_unload: Any = None,
    ) -> None:
        """Declare a VRAM consumer.  Does NOT allocate yet — call ``request()``."""
        self._consumers[name] = VRAMConsumer(
            name=name,
            size_mb=size_mb,
            priority=priority,
            on_unload=on_unload,
        )
        log.debug("registered consumer %r: %d MB, priority %d", name, size_mb, priority)

    # -- budget queries -----------------------------------------------------

    @property
    def total_budget_mb(self) -> int:
        return self._total_budget_mb

    @property
    def allocated_mb(self) -> int:
        """Sum of size_mb for all loaded consumers."""
        return sum(c.size_mb for c in self._consumers.values() if c.is_loaded)

    @property
    def available_mb(self) -> int:
        return self._total_budget_mb - self.allocated_mb

    @property
    def snapshot(self) -> GpuSnapshot | None:
        return self._snapshot

    def status(self) -> dict[str, Any]:
        """Return a summary dict for dashboards and voice reports."""
        return {
            "total_mb": self._total_budget_mb,
            "allocated_mb": self.allocated_mb,
            "available_mb": self.available_mb,
            "consumers": {
                name: {"size_mb": c.size_mb, "loaded": c.is_loaded, "priority": c.priority}
                for name, c in self._consumers.items()
            },
            "gpu_snapshot": {
                "used_mb": self._snapshot.used_mb,
                "free_mb": self._snapshot.free_mb,
                "temperature_c": self._snapshot.temperature_c,
                "utilization_pct": self._snapshot.utilization_pct,
                "gpu_name": self._snapshot.gpu_name,
            }
            if self._snapshot
            else None,
        }

    # -- request / release --------------------------------------------------

    async def request(self, name: str) -> bool:
        """Try to allocate VRAM for consumer *name*.

        If there is not enough room, evict lower-priority consumers
        (highest priority number first) until it fits.  Returns True on
        success, False if eviction was insufficient.

        Raises ``VRAMError`` if the consumer is not registered.
        """
        consumer = self._consumers.get(name)
        if consumer is None:
            raise VRAMError(f"unknown VRAM consumer: {name!r}")
        if consumer.is_loaded:
            return True  # already allocated

        needed = consumer.size_mb
        if needed <= self.available_mb:
            consumer.is_loaded = True
            log.info(
                "VRAM allocated: %s (%d MB), %d MB remaining",
                name,
                needed,
                self.available_mb,
            )
            return True

        # Eviction: sort loaded consumers by priority descending (least
        # important first) and evict until we have room.
        eviction_candidates = sorted(
            (c for c in self._consumers.values() if c.is_loaded and c.name != name),
            key=lambda c: -c.priority,  # highest priority number = least important
        )

        for candidate in eviction_candidates:
            log.info(
                "evicting %s (%d MB, priority %d) to make room for %s",
                candidate.name,
                candidate.size_mb,
                candidate.priority,
                name,
            )
            await self._do_release(candidate)

            if needed <= self.available_mb:
                break

        if needed > self.available_mb:
            log.error(
                "VRAM request failed: %s needs %d MB but only %d MB available after eviction",
                name,
                needed,
                self.available_mb,
            )
            return False

        consumer.is_loaded = True
        log.info(
            "VRAM allocated (after eviction): %s (%d MB), %d MB remaining",
            name,
            needed,
            self.available_mb,
        )
        return True

    async def release(self, name: str) -> None:
        """Release VRAM for consumer *name*."""
        consumer = self._consumers.get(name)
        if consumer is None:
            log.warning("release: unknown consumer %r (ignored)", name)
            return
        if not consumer.is_loaded:
            return
        await self._do_release(consumer)
        log.info("VRAM released: %s (%d MB)", name, consumer.size_mb)

    async def release_all(self) -> None:
        """Release ALL consumers — gaming mode or sleep.

        Called by LifecycleManager when entering SLEEPING mode, or by a
        game-detection hook to free the GPU entirely.
        """
        loaded = [c for c in self._consumers.values() if c.is_loaded]
        for consumer in loaded:
            await self._do_release(consumer)
        if loaded:
            log.info(
                "VRAM release_all: freed %d consumers (%d MB)",
                len(loaded),
                sum(c.size_mb for c in loaded),
            )

    async def refresh_snapshot(self) -> GpuSnapshot | None:
        """Re-read nvidia-smi and update cached snapshot."""
        self._snapshot = await gpu_snapshot()
        return self._snapshot

    # -- internal -----------------------------------------------------------

    async def _do_release(self, consumer: VRAMConsumer) -> None:
        """Unload a single consumer: callback → empty_cache → gc."""
        if consumer.on_unload is not None:
            try:
                result = consumer.on_unload()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("on_unload callback failed for %s", consumer.name)

        consumer.is_loaded = False

        # Mandatory per project rules: always empty_cache + gc.collect
        # after releasing GPU memory.
        _force_gpu_cleanup()


def _force_gpu_cleanup() -> None:
    """torch.cuda.empty_cache() + gc.collect().  No-op if torch is not loaded."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log.debug("torch.cuda.empty_cache() done")
    except ImportError:
        pass
