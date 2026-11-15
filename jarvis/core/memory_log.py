"""Periodic memory log: how much RAM Jarvis uses and how much is left.

Live session 2026-11-03 18:42: in the same second the wake word raised
MemoryError inside Jarvis and Ollama answered HTTP 500, and nothing in the
logs said who had used the memory or when it started to run out. One line a
minute ("[mem] jarvis rss=… MB, system free=… MB of … MB") makes that
readable after the fact; a WARNING marks the moment free memory gets low.

psutil is a core dependency; without it the loop logs once and stops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

INTERVAL_S: float = 60.0
# Below this much free system memory a WARNING is written instead of INFO.
LOW_FREE_MB: int = 1500


@dataclass(frozen=True, slots=True)
class MemorySample:
    rss_mb: int
    free_mb: int
    total_mb: int


def read_memory() -> MemorySample:
    """This process's resident set and the system's free / total memory."""
    import psutil

    vm = psutil.virtual_memory()
    rss = psutil.Process().memory_info().rss
    mb = 1024 * 1024
    return MemorySample(rss_mb=rss // mb, free_mb=vm.available // mb, total_mb=vm.total // mb)


def log_sample(sample: MemorySample) -> None:
    level = logging.WARNING if sample.free_mb < LOW_FREE_MB else logging.INFO
    log.log(
        level,
        "[mem] jarvis rss=%d MB, system free=%d MB of %d MB%s",
        sample.rss_mb, sample.free_mb, sample.total_mb,
        " — memory is running low" if level == logging.WARNING else "",
    )


async def run(
    *,
    interval_s: float = INTERVAL_S,
    read: Callable[[], MemorySample] = read_memory,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Log a sample now and then every *interval_s* until cancelled."""
    while True:
        try:
            log_sample(read())
        except ImportError:
            log.info("[mem] psutil not installed; memory is not logged")
            return
        except Exception:
            log.debug("[mem] could not read memory", exc_info=True)
        await sleep(interval_s)
