"""Tests for the PiperTTS LRU voice cache (Task 7.2).

No real Piper: piper.PiperVoice.load and jarvis.audio.tts.sd are mocked, so
these exercise the cache/activate/preload/evict logic and the stream-reopen
path without loading a model or opening a device. Real latency/memory
numbers are verified on the home PC (see "проверить дома" in the report).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.audio.tts import PiperTTS, TTSLoadError


def _make_fake_stream() -> MagicMock:
    s = MagicMock()
    s.active = False
    s.start.side_effect = lambda: setattr(s, "active", True)
    s.stop.side_effect = lambda: setattr(s, "active", False)
    s.abort.side_effect = lambda: setattr(s, "active", False)
    return s


def _make_fake_voice(sample_rate: int = 22050) -> MagicMock:
    v = MagicMock()
    v.config.sample_rate = sample_rate
    return v


def _write_voice_files(voices_dir: Path, *names: str) -> None:
    for name in names:
        (voices_dir / f"{name}.onnx").write_bytes(b"fake")
        (voices_dir / f"{name}.onnx.json").write_text("{}")


@pytest.fixture
def voices_dir(tmp_path) -> Path:
    _write_voice_files(
        tmp_path,
        "en_GB-alan-medium",
        "ru_RU-dmitri-medium",
        "uk_UA-mykyta-high",
        "uk_UA-lada-x_low",
    )
    return tmp_path


def _load_by_path(rates: dict[str, int]):
    """Return a PiperVoice.load stand-in that hands back a fake voice whose
    sample_rate depends on the voice file requested (so we can assert the
    ACTUAL model rate is used, not the profile's nominal rate)."""
    def _load(path: str) -> MagicMock:
        for name, rate in rates.items():
            if name in path:
                return _make_fake_voice(sample_rate=rate)
        return _make_fake_voice(sample_rate=22050)
    return _load


@pytest.mark.asyncio
async def test_activate_switches_voice_and_opens_stream(voices_dir) -> None:
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"dmitri": 22050, "alan": 22050}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        await tts.activate_voice("ru_RU-dmitri-medium")
        assert tts.voice_name == "ru_RU-dmitri-medium"
        assert tts.is_loaded


@pytest.mark.asyncio
async def test_switch_back_is_cache_hit_no_reload(voices_dir) -> None:
    """Returning to a previously loaded voice must NOT call PiperVoice.load
    again — that is the whole point of the cache."""
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"alan": 22050, "dmitri": 22050}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()  # load #1: alan
        await tts.activate_voice("ru_RU-dmitri-medium")  # load #2: dmitri
        assert voice_cls.load.call_count == 2
        await tts.activate_voice("en_GB-alan-medium")  # cache hit: no load
        assert voice_cls.load.call_count == 2
        assert tts.voice_name == "en_GB-alan-medium"


@pytest.mark.asyncio
async def test_actual_model_rate_used_not_profile(voices_dir) -> None:
    """uk_UA-lada-x_low is really 16000 Hz; the stream must open at the
    model's actual rate, not an assumed 22050."""
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"alan": 22050, "lada": 16000}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        await tts.activate_voice("uk_UA-lada-x_low")
        assert tts._sample_rate == 16000


@pytest.mark.asyncio
async def test_capacity_evicts_lru_but_keeps_active(voices_dir) -> None:
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"alan": 22050, "dmitri": 22050, "mykyta": 22050, "lada": 16000}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        # Capacity 2: at most two voices resident.
        tts = PiperTTS(
            voice_name="en_GB-alan-medium",
            voices_dir=voices_dir,
            voice_cache_capacity=2,
        )
        await tts.load()  # cache: {alan}
        await tts.activate_voice("ru_RU-dmitri-medium")  # {alan, dmitri}
        await tts.activate_voice("uk_UA-mykyta-high")  # evict alan -> {dmitri, mykyta}
        assert set(tts._voice_cache) == {"ru_RU-dmitri-medium", "uk_UA-mykyta-high"}
        # Active voice (mykyta) is never evicted even when it is the LRU.
        await tts.activate_voice("uk_UA-lada-x_low")  # evict dmitri -> {mykyta, lada}
        assert "uk_UA-mykyta-high" in tts._voice_cache
        assert set(tts._voice_cache) == {"uk_UA-mykyta-high", "uk_UA-lada-x_low"}


@pytest.mark.asyncio
async def test_preload_warms_cache_without_activating(voices_dir) -> None:
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"alan": 22050, "dmitri": 22050}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        await tts.preload_voice("ru_RU-dmitri-medium")
        # Cached but NOT active.
        assert "ru_RU-dmitri-medium" in tts._voice_cache
        assert tts.voice_name == "en_GB-alan-medium"
        # Now switching is a cache hit.
        before = voice_cls.load.call_count
        await tts.activate_voice("ru_RU-dmitri-medium")
        assert voice_cls.load.call_count == before


@pytest.mark.asyncio
async def test_unload_clears_all_cached_models(voices_dir) -> None:
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path(
            {"alan": 22050, "dmitri": 22050}
        )
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        await tts.activate_voice("ru_RU-dmitri-medium")
        assert len(tts._voice_cache) == 2
        await tts.unload()
        assert len(tts._voice_cache) == 0
        assert tts._stream is None
        assert not tts.is_loaded


@pytest.mark.asyncio
async def test_activate_logs_timing_breakdown(voices_dir, caplog) -> None:
    import logging

    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path({"alan": 22050, "dmitri": 22050})
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        with caplog.at_level(logging.INFO, logger="jarvis.audio.tts"):
            await tts.activate_voice("ru_RU-dmitri-medium")
        line = next(
            (r.getMessage() for r in caplog.records if "voice switch:" in r.getMessage()),
            None,
        )
        assert line is not None
        assert "model_load=" in line and "stream_open=" in line and "total=" in line
        assert "cache miss" in line
        # Second activation of the same voice reports a cache hit.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="jarvis.audio.tts"):
            await tts.activate_voice("en_GB-alan-medium")  # cache hit
            await tts.activate_voice("ru_RU-dmitri-medium")  # cache hit
        hits = [r.getMessage() for r in caplog.records if "cache hit" in r.getMessage()]
        assert hits


@pytest.mark.asyncio
async def test_missing_voice_activation_raises(voices_dir) -> None:
    with patch("piper.PiperVoice") as voice_cls, \
         patch("jarvis.audio.tts.sd") as sd_mock:
        voice_cls.load.side_effect = _load_by_path({"alan": 22050})
        sd_mock.RawOutputStream.side_effect = lambda **_k: _make_fake_stream()

        tts = PiperTTS(voice_name="en_GB-alan-medium", voices_dir=voices_dir)
        await tts.load()
        with pytest.raises(TTSLoadError):
            await tts.activate_voice("nonexistent-voice")
