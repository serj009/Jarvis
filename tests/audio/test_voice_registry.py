"""Tests for jarvis.audio.voice_registry and tts_manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis.audio.tts_manager import TTSManager
from jarvis.audio.voice_registry import (
    BUILTIN_VOICES,
    VoiceBackend,
    VoiceProfile,
    VoiceRegistry,
)


class TestVoiceRegistry:
    def test_defaults(self) -> None:
        reg = VoiceRegistry()
        assert reg.get_voice_name("en") == "en_GB-alan-medium"
        assert reg.get_voice_name("ru") == "ru_RU-dmitri-medium"
        assert reg.get_voice_name("uk") == "uk_UA-mykyta-high"

    def test_set_voice(self) -> None:
        reg = VoiceRegistry()
        reg.set_voice("ru", "ru_RU-irina-medium")
        assert reg.get_voice_name("ru") == "ru_RU-irina-medium"

    def test_unknown_language_falls_back_to_en(self) -> None:
        reg = VoiceRegistry()
        voice = reg.get_voice("fr")
        assert voice is not None
        assert voice.language == "en"

    def test_register_custom_voice(self) -> None:
        reg = VoiceRegistry()
        custom = VoiceProfile(
            name="ru_RU-baranov-custom",
            language="ru",
            display_name="Baranov",
            is_custom=True,
        )
        reg.register(custom)
        reg.set_voice("ru", "ru_RU-baranov-custom")
        assert reg.get_voice_name("ru") == "ru_RU-baranov-custom"
        voice = reg.get_voice("ru")
        assert voice is not None
        assert voice.is_custom is True

    def test_list_voices(self) -> None:
        reg = VoiceRegistry()
        all_voices = reg.list_voices()
        assert len(all_voices) == len(BUILTIN_VOICES)

    def test_list_by_language(self) -> None:
        reg = VoiceRegistry()
        ru_voices = reg.list_voices("ru")
        assert all(v.language == "ru" for v in ru_voices)
        assert len(ru_voices) >= 1

    def test_is_downloaded(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        assert not reg.is_downloaded("en_GB-alan-medium")

        # Create fake voice files
        (tmp_path / "en_GB-alan-medium.onnx").touch()
        (tmp_path / "en_GB-alan-medium.onnx.json").touch()
        assert reg.is_downloaded("en_GB-alan-medium")

    def test_list_available(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        (tmp_path / "en_GB-alan-medium.onnx").touch()
        (tmp_path / "en_GB-alan-medium.onnx.json").touch()
        available = reg.list_available("en")
        assert len(available) == 1
        assert available[0].name == "en_GB-alan-medium"

    def test_assignments(self) -> None:
        reg = VoiceRegistry()
        a = reg.assignments
        assert "en" in a
        assert "ru" in a
        assert "uk" in a

    def test_unregister(self) -> None:
        reg = VoiceRegistry()
        reg.unregister("en_GB-alan-medium")
        voice = reg.get_voice("en")
        # Should still have a name in assignments, but profile is None
        assert voice is None


class TestVoiceProfile:
    def test_piper_backend(self) -> None:
        v = VoiceProfile(name="test", language="en")
        assert v.backend == VoiceBackend.PIPER

    def test_frozen(self) -> None:
        v = VoiceProfile(name="test", language="en")
        try:
            v.name = "changed"  # type: ignore
            raise AssertionError("should be frozen")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Задача 3 — voice path helper + background warmup (commit B)
# ---------------------------------------------------------------------------


class TestVoicePath:
    def test_voice_path_none_when_not_downloaded(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        assert reg.voice_path("en_GB-alan-medium") is None

    def test_voice_path_returns_onnx_when_downloaded(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        (tmp_path / "en_GB-alan-medium.onnx").touch()
        (tmp_path / "en_GB-alan-medium.onnx.json").touch()
        path = reg.voice_path("en_GB-alan-medium")
        assert path == tmp_path / "en_GB-alan-medium.onnx"


def _downloaded_registry(tmp_path: Path) -> VoiceRegistry:
    """A registry whose en/ru/uk voices are all 'downloaded' (touch files)."""
    reg = VoiceRegistry(voices_dir=tmp_path)
    for lang in ("en", "ru", "uk"):
        name = reg.get_voice_name(lang)
        (tmp_path / f"{name}.onnx").touch()
        (tmp_path / f"{name}.onnx.json").touch()
    return reg


class TestTTSManagerWarmup:
    @pytest.mark.asyncio
    async def test_warmup_does_not_block_and_loads_in_background(
        self, tmp_path, monkeypatch
    ) -> None:
        """warmup() returns immediately; the slow per-voice load runs in the
        background task, not inline."""
        reg = _downloaded_registry(tmp_path)
        piper = MagicMock()
        piper.voice_name = reg.get_voice_name("en")  # current = en
        mgr = TTSManager(piper_tts=piper, voice_registry=reg)

        loaded: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_load(voice_name: str) -> None:
            started.set()
            await release.wait()  # block until the test lets go
            loaded.append(voice_name)

        monkeypatch.setattr(mgr, "_warmup_one", slow_load)

        task = mgr.warmup()
        assert task is not None
        # Nothing loaded yet — warmup did NOT block on the slow load.
        assert loaded == []
        # Let the background task proceed and finish.
        await asyncio.wait_for(started.wait(), timeout=1.0)
        release.set()
        await asyncio.wait_for(task, timeout=1.0)
        # ru + uk warmed (en is current → skipped).
        assert set(loaded) == {
            reg.get_voice_name("ru"), reg.get_voice_name("uk"),
        }

    @pytest.mark.asyncio
    async def test_warmup_is_resilient_to_load_exceptions(
        self, tmp_path, monkeypatch
    ) -> None:
        """A per-voice load error is logged and swallowed; the task still
        completes and later voices are still attempted."""
        reg = _downloaded_registry(tmp_path)
        piper = MagicMock()
        piper.voice_name = reg.get_voice_name("en")
        mgr = TTSManager(piper_tts=piper, voice_registry=reg)

        attempted: list[str] = []

        async def boom(voice_name: str) -> None:
            attempted.append(voice_name)
            raise RuntimeError("corrupt voice model")

        monkeypatch.setattr(mgr, "_warmup_one", boom)
        task = mgr.warmup()
        assert task is not None
        await asyncio.wait_for(task, timeout=1.0)  # must not raise
        # Both non-current voices were attempted despite the first failing.
        assert set(attempted) == {
            reg.get_voice_name("ru"), reg.get_voice_name("uk"),
        }

    @pytest.mark.asyncio
    async def test_warmup_skipped_when_game_mode_active(
        self, tmp_path, monkeypatch
    ) -> None:
        """warmup() returns None and loads nothing when game mode is active."""
        reg = _downloaded_registry(tmp_path)
        piper = MagicMock()
        piper.voice_name = reg.get_voice_name("en")
        mgr = TTSManager(piper_tts=piper, voice_registry=reg)

        called = False

        async def should_not_run(voice_name: str) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(mgr, "_warmup_one", should_not_run)
        task = mgr.warmup(is_game_mode_active=lambda: True)
        assert task is None
        assert called is False

    @pytest.mark.asyncio
    async def test_warmup_skips_not_downloaded_voices(
        self, tmp_path, monkeypatch
    ) -> None:
        """Voices that are not downloaded are not warmed."""
        reg = VoiceRegistry(voices_dir=tmp_path)
        # Only ru is downloaded.
        ru = reg.get_voice_name("ru")
        (tmp_path / f"{ru}.onnx").touch()
        (tmp_path / f"{ru}.onnx.json").touch()
        piper = MagicMock()
        piper.voice_name = reg.get_voice_name("en")  # current = en (not downloaded)
        mgr = TTSManager(piper_tts=piper, voice_registry=reg)

        loaded: list[str] = []

        async def track(voice_name: str) -> None:
            loaded.append(voice_name)

        monkeypatch.setattr(mgr, "_warmup_one", track)
        task = mgr.warmup()
        assert task is not None
        await asyncio.wait_for(task, timeout=1.0)
        assert loaded == [ru]  # only the downloaded, non-current voice
