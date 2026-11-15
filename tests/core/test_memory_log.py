"""jarvis.core.memory_log: one memory line a minute, a WARNING when low."""

from __future__ import annotations

import asyncio
import logging

import pytest

from jarvis.core import memory_log
from jarvis.core.memory_log import MemorySample


async def _run_ticks(samples: list[MemorySample]) -> None:
    """Drive run() through len(samples) ticks without real sleeping."""
    feed = iter(samples)
    ticks = 0

    async def sleep(_s: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= len(samples):
            raise asyncio.CancelledError
        await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await memory_log.run(read=lambda: next(feed), sleep=sleep)


async def test_logs_rss_and_free_memory_each_tick(caplog):
    caplog.set_level(logging.INFO, logger="jarvis.core.memory_log")
    await _run_ticks([MemorySample(900, 12000, 32000), MemorySample(950, 11800, 32000)])
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "[mem] jarvis rss=900 MB, system free=12000 MB of 32000 MB",
        "[mem] jarvis rss=950 MB, system free=11800 MB of 32000 MB",
    ]


async def test_low_free_memory_is_a_warning(caplog):
    caplog.set_level(logging.INFO, logger="jarvis.core.memory_log")
    await _run_ticks([MemorySample(4000, 800, 32000)])
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "memory is running low" in record.getMessage()


def test_read_memory_reads_this_process():
    sample = memory_log.read_memory()
    assert 0 < sample.rss_mb < sample.total_mb
    assert 0 < sample.free_mb <= sample.total_mb
