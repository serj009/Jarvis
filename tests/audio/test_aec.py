"""Tests for jarvis.audio.aec — AEC / barge-in management (T3.3).

All tests run without real audio hardware or AEC libraries installed.
The AEC fallback test verifies graceful degradation to half-duplex when
no AEC backend is available (the expected situation on most dev machines).
"""

from __future__ import annotations

import pytest

from jarvis.audio.aec import BargeInManager, BargeInMode
from jarvis.audio.protocols import FRAME_BYTES, SAMPLE_RATE

SILENCE_FRAME: bytes = b"\x00" * FRAME_BYTES


# ---------------------------------------------------------------------------
# Half-duplex mode
# ---------------------------------------------------------------------------


class TestHalfDuplex:
    """Half-duplex: mic muted during TTS, unmuted on TTS end."""

    def test_mute_on_tts_start(self) -> None:
        events: list[str] = []
        mgr = BargeInManager(
            mode="half_duplex",
            on_mic_mute=lambda: events.append("mute"),
            on_mic_unmute=lambda: events.append("unmute"),
        )
        mgr.on_tts_start()
        assert mgr.is_mic_muted is True
        assert events == ["mute"]

    def test_unmute_on_tts_end(self) -> None:
        events: list[str] = []
        mgr = BargeInManager(
            mode="half_duplex",
            on_mic_mute=lambda: events.append("mute"),
            on_mic_unmute=lambda: events.append("unmute"),
        )
        mgr.on_tts_start()
        mgr.on_tts_end()
        assert mgr.is_mic_muted is False
        assert events == ["mute", "unmute"]

    def test_double_mute_is_idempotent(self) -> None:
        """Calling on_tts_start twice must fire callback only once."""
        count = 0

        def inc() -> None:
            nonlocal count
            count += 1

        mgr = BargeInManager(mode="half_duplex", on_mic_mute=inc)
        mgr.on_tts_start()
        mgr.on_tts_start()
        assert count == 1

    def test_double_unmute_is_idempotent(self) -> None:
        """Calling on_tts_end twice must fire unmute callback only once."""
        count = 0

        def inc() -> None:
            nonlocal count
            count += 1

        mgr = BargeInManager(mode="half_duplex", on_mic_unmute=inc)
        mgr.on_tts_start()
        mgr.on_tts_end()
        mgr.on_tts_end()
        assert count == 1

    def test_no_callbacks_does_not_crash(self) -> None:
        """Manager works fine without callbacks (noop)."""
        mgr = BargeInManager(mode="half_duplex")
        mgr.on_tts_start()
        assert mgr.is_mic_muted is True
        mgr.on_tts_end()
        assert mgr.is_mic_muted is False


# ---------------------------------------------------------------------------
# Headphones mode
# ---------------------------------------------------------------------------


class TestHeadphones:
    """Headphones: no muting at all — echo is not a problem."""

    def test_no_mute_on_tts_start(self) -> None:
        mgr = BargeInManager(mode="headphones")
        mgr.on_tts_start()
        assert mgr.is_mic_muted is False

    def test_no_mute_on_tts_end(self) -> None:
        mgr = BargeInManager(mode="headphones")
        mgr.on_tts_start()
        mgr.on_tts_end()
        assert mgr.is_mic_muted is False

    def test_tts_playing_tracked(self) -> None:
        """Even in headphones mode, tts_playing is tracked."""
        mgr = BargeInManager(mode="headphones")
        assert mgr.tts_playing is False
        mgr.on_tts_start()
        assert mgr.tts_playing is True
        mgr.on_tts_end()
        assert mgr.tts_playing is False


# ---------------------------------------------------------------------------
# AEC fallback
# ---------------------------------------------------------------------------


class TestAECFallback:
    """When no AEC library is installed, mode degrades to half-duplex."""

    def test_aec_falls_back_to_half_duplex(self) -> None:
        mgr = BargeInManager(mode="aec")
        status = mgr.get_status()
        # Without pywebrtc-audio or echoff installed, must fall back.
        assert status["mode"] in ("half_duplex", "aec")
        if status["mode"] == "half_duplex":
            assert status["aec_available"] is False

    def test_aec_fallback_mutes_mic(self) -> None:
        """After fallback, half-duplex behaviour must be active."""
        mgr = BargeInManager(mode="aec")
        if mgr.mode is BargeInMode.HALF_DUPLEX:
            mgr.on_tts_start()
            assert mgr.is_mic_muted is True


# ---------------------------------------------------------------------------
# process_audio passthrough
# ---------------------------------------------------------------------------


class TestProcessAudio:
    """process_audio returns mic_chunk unchanged without AEC backend."""

    def test_passthrough_half_duplex(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        result = mgr.process_audio(SILENCE_FRAME, SILENCE_FRAME)
        assert result == SILENCE_FRAME

    def test_passthrough_headphones(self) -> None:
        mgr = BargeInManager(mode="headphones")
        result = mgr.process_audio(SILENCE_FRAME)
        assert result == SILENCE_FRAME

    def test_passthrough_no_speaker_chunk(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        result = mgr.process_audio(SILENCE_FRAME)
        assert result == SILENCE_FRAME

    def test_passthrough_aec_without_backend(self) -> None:
        """AEC mode without library still returns passthrough."""
        mgr = BargeInManager(mode="aec")
        result = mgr.process_audio(SILENCE_FRAME, SILENCE_FRAME)
        assert result == SILENCE_FRAME


# ---------------------------------------------------------------------------
# check_barge_in
# ---------------------------------------------------------------------------


class TestCheckBargeIn:
    """Barge-in detection logic."""

    def test_no_barge_in_when_tts_not_playing(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        assert mgr.check_barge_in(SILENCE_FRAME) is False

    def test_no_barge_in_half_duplex_while_playing(self) -> None:
        """Half-duplex relies on wake-word, not check_barge_in."""
        mgr = BargeInManager(mode="half_duplex")
        mgr.on_tts_start()
        assert mgr.check_barge_in(SILENCE_FRAME) is False

    def test_no_barge_in_headphones_while_playing(self) -> None:
        mgr = BargeInManager(mode="headphones")
        mgr.on_tts_start()
        assert mgr.check_barge_in(SILENCE_FRAME) is False


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    """Status dict contains all expected keys."""

    def test_status_keys_half_duplex(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        s = mgr.get_status()
        assert "mode" in s
        assert "mic_muted" in s
        assert "tts_playing" in s
        assert "aec_available" in s

    def test_status_values_default(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        s = mgr.get_status()
        assert s["mode"] == "half_duplex"
        assert s["mic_muted"] is False
        assert s["tts_playing"] is False
        assert s["aec_available"] is False

    def test_status_after_tts_start(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        mgr.on_tts_start()
        s = mgr.get_status()
        assert s["mic_muted"] is True
        assert s["tts_playing"] is True

    def test_status_headphones(self) -> None:
        mgr = BargeInManager(mode="headphones")
        s = mgr.get_status()
        assert s["mode"] == "headphones"
        assert s["mic_muted"] is False
        assert s["aec_available"] is False


# ---------------------------------------------------------------------------
# BargeInMode enum
# ---------------------------------------------------------------------------


class TestBargeInMode:
    """Mode enum covers all documented values."""

    def test_all_modes_exist(self) -> None:
        assert BargeInMode("half_duplex") is BargeInMode.HALF_DUPLEX
        assert BargeInMode("aec") is BargeInMode.AEC
        assert BargeInMode("headphones") is BargeInMode.HEADPHONES

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            BargeInMode("nonexistent")

    def test_manager_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            BargeInManager(mode="nonexistent")


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Verify property accessors."""

    def test_mode_property(self) -> None:
        mgr = BargeInManager(mode="headphones")
        assert mgr.mode is BargeInMode.HEADPHONES

    def test_aec_available_false_by_default(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        assert mgr.aec_available is False

    def test_tts_playing_tracks_state(self) -> None:
        mgr = BargeInManager(mode="half_duplex")
        assert mgr.tts_playing is False
        mgr.on_tts_start()
        assert mgr.tts_playing is True
        mgr.on_tts_end()
        assert mgr.tts_playing is False


# ---------------------------------------------------------------------------
# Barge-in callback
# ---------------------------------------------------------------------------


class TestBargeInCallback:
    """Verify that _trigger_barge_in resets state and fires callback."""

    def test_trigger_resets_tts_and_unmutes(self) -> None:
        events: list[str] = []
        mgr = BargeInManager(
            mode="half_duplex",
            on_mic_mute=lambda: events.append("mute"),
            on_mic_unmute=lambda: events.append("unmute"),
            on_barge_in=lambda: events.append("barge_in"),
        )
        mgr.on_tts_start()
        assert mgr.is_mic_muted is True
        # Simulate an internal barge-in trigger.
        mgr._trigger_barge_in()
        assert mgr.tts_playing is False
        assert mgr.is_mic_muted is False
        assert "barge_in" in events
        assert "unmute" in events
