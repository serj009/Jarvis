"""Tests for jarvis.core.vram_manager (T1.4 VRAM Manager)."""

from __future__ import annotations

import pytest

from jarvis.core.errors import VRAMError
from jarvis.core.vram_manager import GpuSnapshot, VRAMManager


class TestVRAMManager:
    @pytest.fixture()
    def mgr(self) -> VRAMManager:
        return VRAMManager(total_budget_mb=12_000)

    def test_initial_state(self, mgr: VRAMManager) -> None:
        assert mgr.total_budget_mb == 12_000
        assert mgr.allocated_mb == 0
        assert mgr.available_mb == 12_000

    @pytest.mark.asyncio()
    async def test_register_and_request(self, mgr: VRAMManager) -> None:
        mgr.register("ollama", size_mb=8000, priority=10)
        ok = await mgr.request("ollama")
        assert ok is True
        assert mgr.allocated_mb == 8000
        assert mgr.available_mb == 4000

    @pytest.mark.asyncio()
    async def test_request_unknown_raises(self, mgr: VRAMManager) -> None:
        with pytest.raises(VRAMError, match="unknown"):
            await mgr.request("nonexistent")

    @pytest.mark.asyncio()
    async def test_double_request_is_noop(self, mgr: VRAMManager) -> None:
        mgr.register("tts", size_mb=2500, priority=20)
        await mgr.request("tts")
        ok = await mgr.request("tts")
        assert ok is True
        assert mgr.allocated_mb == 2500  # not doubled

    @pytest.mark.asyncio()
    async def test_release(self, mgr: VRAMManager) -> None:
        mgr.register("tts", size_mb=2500, priority=20)
        await mgr.request("tts")
        await mgr.release("tts")
        assert mgr.allocated_mb == 0
        assert mgr.available_mb == 12_000

    @pytest.mark.asyncio()
    async def test_release_unknown_is_warning(self, mgr: VRAMManager) -> None:
        # Should not raise
        await mgr.release("nonexistent")

    @pytest.mark.asyncio()
    async def test_eviction_frees_lower_priority(self, mgr: VRAMManager) -> None:
        mgr.register("ollama", size_mb=8000, priority=10)
        mgr.register("tts", size_mb=2500, priority=20)
        mgr.register("vision", size_mb=5000, priority=30)  # least important

        await mgr.request("ollama")
        await mgr.request("tts")
        # Now 10500 MB used, 1500 free. Vision needs 5000.
        # Should evict tts (priority 20 > 10, but vision is 30 so tts is candidate)
        ok = await mgr.request("vision")
        assert ok is True
        # tts was evicted (priority 20 — higher number than ollama's 10)
        status = mgr.status()
        assert status["consumers"]["tts"]["loaded"] is False
        assert status["consumers"]["vision"]["loaded"] is True
        assert status["consumers"]["ollama"]["loaded"] is True

    @pytest.mark.asyncio()
    async def test_eviction_fails_if_not_enough(self, mgr: VRAMManager) -> None:
        mgr_small = VRAMManager(total_budget_mb=4000)
        mgr_small.register("a", size_mb=3000, priority=10)
        mgr_small.register("b", size_mb=3000, priority=20)

        await mgr_small.request("a")
        # b needs 3000, only 1000 free, evicting a gives 4000 total — enough
        ok = await mgr_small.request("b")
        assert ok is True

    @pytest.mark.asyncio()
    async def test_eviction_truly_insufficient(self) -> None:
        mgr = VRAMManager(total_budget_mb=2000)
        mgr.register("big", size_mb=3000, priority=10)
        ok = await mgr.request("big")
        assert ok is False

    @pytest.mark.asyncio()
    async def test_release_all(self, mgr: VRAMManager) -> None:
        mgr.register("ollama", size_mb=8000, priority=10)
        mgr.register("tts", size_mb=2500, priority=20)
        await mgr.request("ollama")
        await mgr.request("tts")
        assert mgr.allocated_mb == 10_500

        await mgr.release_all()
        assert mgr.allocated_mb == 0

    @pytest.mark.asyncio()
    async def test_on_unload_callback(self, mgr: VRAMManager) -> None:
        called = []

        async def _on_unload() -> None:
            called.append(True)

        mgr.register("tts", size_mb=2500, priority=20, on_unload=_on_unload)
        await mgr.request("tts")
        await mgr.release("tts")
        assert called == [True]

    def test_status_dict(self, mgr: VRAMManager) -> None:
        mgr.register("ollama", size_mb=8000, priority=10)
        status = mgr.status()
        assert "total_mb" in status
        assert "consumers" in status
        assert "ollama" in status["consumers"]

    @pytest.mark.asyncio()
    async def test_loadable_protocol(self, mgr: VRAMManager) -> None:
        assert mgr.is_loaded is False
        await mgr.load()
        assert mgr.is_loaded is True
        await mgr.unload()
        assert mgr.is_loaded is False


class TestGpuSnapshot:
    def test_frozen(self) -> None:
        snap = GpuSnapshot(total_mb=12000, used_mb=8000, free_mb=4000)
        with pytest.raises(AttributeError):
            snap.total_mb = 999  # type: ignore[misc]
