"""Tests for jarvis.tools.local.system_stats.SystemStatsTool."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from jarvis.core.vram_manager import GpuSnapshot, VRAMManager
from jarvis.tools.local.system_stats import SystemStatsTool
from jarvis.tools.registry import EmptyArgs
from tests._typing import fake_module, output_text


def _fake_psutil(cpu: float, mem: float) -> types.ModuleType:
    vm = MagicMock()
    vm.percent = mem
    return fake_module(
        "psutil",
        cpu_percent=MagicMock(return_value=cpu),
        virtual_memory=MagicMock(return_value=vm),
    )


# ---------------------------------------------------------------------------
# Original tests (backward-compatible: no VRAM manager)
# ---------------------------------------------------------------------------


async def test_returns_cpu_and_memory_in_spoken_summary():
    with patch.dict(sys.modules, {"psutil": _fake_psutil(47.3, 62.0)}):
        result = await SystemStatsTool().execute(EmptyArgs())
    assert result.success
    out = output_text(result)
    assert "47" in out
    assert "62" in out
    assert "sir" in out.lower() or "сэр" in out.lower() or "сер" in out.lower()


async def test_failure_returns_error():
    fake = fake_module(
        "psutil", cpu_percent=MagicMock(side_effect=RuntimeError("counter not ready"))
    )
    with patch.dict(sys.modules, {"psutil": fake}):
        result = await SystemStatsTool().execute(EmptyArgs())
    assert not result.success
    assert "counter not ready" in (result.error or "")


def test_requires_confirmation_false():
    assert SystemStatsTool().requires_confirmation is False


# ---------------------------------------------------------------------------
# New tests: with VRAMManager (T1.3 / T1.4 integration)
# ---------------------------------------------------------------------------


def _make_vram_manager() -> VRAMManager:
    """Create a VRAMManager with a fake snapshot (no nvidia-smi needed)."""
    mgr = VRAMManager(total_budget_mb=12_000)
    mgr._snapshot = GpuSnapshot(
        total_mb=12288,
        used_mb=8500,
        free_mb=3788,
        gpu_name="NVIDIA GeForce RTX 3060",
        temperature_c=62,
        utilization_pct=45,
    )
    return mgr


async def test_gpu_info_included_with_vram_manager():
    mgr = _make_vram_manager()
    mgr.register("ollama", size_mb=8000, priority=10)
    await mgr.request("ollama")

    with patch.dict(sys.modules, {"psutil": _fake_psutil(30.0, 50.0)}):
        # Patch refresh_snapshot to return existing snapshot (no nvidia-smi)
        with patch.object(mgr, "refresh_snapshot", new_callable=AsyncMock, return_value=mgr.snapshot):
            result = await SystemStatsTool(vram_manager=mgr).execute(EmptyArgs())

    assert result.success
    out = output_text(result)
    # Should contain GPU memory info
    assert "12288" in out or "12 288" in out
    # Should contain loaded model name
    assert "ollama" in out.lower()


async def test_no_gpu_info_without_vram_manager():
    with patch.dict(sys.modules, {"psutil": _fake_psutil(10.0, 20.0)}):
        result = await SystemStatsTool(vram_manager=None).execute(EmptyArgs())
    out = output_text(result)
    # Should mention GPU not available
    assert "not available" in out.lower() or "недоступна" in out.lower()
