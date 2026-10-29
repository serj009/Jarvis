"""Tests for jarvis.audio.device_detector — smart audio device detection.

sounddevice is fully stubbed; no real audio hardware is touched.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.audio.device_detector import (
    RankedDevice,
    _is_virtual,
    _score_input_device,
    _score_output_device,
    deduplicate_devices,
    detect_best_input,
    detect_best_output,
    list_real_inputs,
    list_real_outputs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dev(
    name: str,
    *,
    max_input_channels: int = 0,
    max_output_channels: int = 0,
    hostapi: int = 0,
    default_samplerate: float = 48000.0,
) -> dict:
    return {
        "name": name,
        "max_input_channels": max_input_channels,
        "max_output_channels": max_output_channels,
        "hostapi": hostapi,
        "default_samplerate": default_samplerate,
    }


def _hostapis(*names: str) -> list[dict]:
    return [{"name": n} for n in names]


WASAPI = 0
MME = 1
DS = 2  # DirectSound

STANDARD_HOSTAPIS = _hostapis("Windows WASAPI", "MME", "Windows DirectSound")


@pytest.fixture
def patch_sd():
    """Patch sounddevice inside device_detector for controlled enumeration."""
    with patch("jarvis.audio.device_detector.sd") as sd_mock:
        sd_mock.query_hostapis.return_value = STANDARD_HOSTAPIS
        yield sd_mock


# ---------------------------------------------------------------------------
# Virtual device detection
# ---------------------------------------------------------------------------


class TestIsVirtual:
    @pytest.mark.parametrize(
        "name",
        [
            "Stereo Mix (Realtek HD Audio)",
            "What U Hear (Creative SB)",
            "CABLE Input (VB-Audio Virtual Cable)",
            "CABLE Output (VB-Audio Virtual Cable)",
            "Voicemeeter Input",
            "Loopback Device",
            "Virtual Audio Cable",
            "NVIDIA Broadcast",
            "NVIDIA RTX Voice",
            "Remote Audio",
            "Steam Streaming Speakers",
            "Digital Audio (S/PDIF)",
            "Realtek Digital Output (SPDIF)",
        ],
    )
    def test_virtual_device_detected(self, name: str) -> None:
        assert _is_virtual(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Microphone (Realtek HD Audio)",
            "Microphone (TONOR TM20 Audio Device)",
            "Speakers (Realtek HD Audio)",
            "Headphones (Corsair HS70)",
            "USB Audio Device",
        ],
    )
    def test_physical_device_not_virtual(self, name: str) -> None:
        assert _is_virtual(name) is False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_physical_mic_scores_higher_than_virtual(self) -> None:
        real = _score_input_device(
            "Microphone (USB Audio)", "Windows WASAPI", 1, 48000,
        )
        virtual = _score_input_device(
            "Stereo Mix (Realtek)", "Windows WASAPI", 2, 48000,
        )
        # The real mic has "microphone" + "usb" indicators.
        assert real > virtual

    def test_wasapi_preferred_over_mme(self) -> None:
        wasapi = _score_input_device(
            "Microphone (Realtek)", "Windows WASAPI", 1, 48000,
        )
        mme = _score_input_device(
            "Microphone (Realtek)", "MME", 1, 48000,
        )
        assert wasapi > mme
        # WASAPI bonus is +10; same device via MME should be exactly 10 lower.
        assert wasapi - mme == pytest.approx(10.0)

    def test_usb_mic_bonus(self) -> None:
        usb = _score_input_device(
            "USB Microphone", "Windows WASAPI", 1, 48000,
        )
        builtin = _score_input_device(
            "Microphone (Realtek)", "Windows WASAPI", 1, 48000,
        )
        # USB gets an extra +3 on top of the shared bonuses.
        assert usb > builtin

    def test_output_stereo_preferred(self) -> None:
        stereo = _score_output_device("Speakers (HD Audio)", "WASAPI", 2, 48000)
        mono = _score_output_device("Speakers (HD Audio)", "WASAPI", 1, 48000)
        assert stereo > mono


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_deduplication_keeps_highest_scored(self) -> None:
        wasapi = RankedDevice(
            index=1, name="Microphone (Realtek)",
            host_api="WASAPI", channels=1, sample_rate=48000,
            score=18.5, is_virtual=False,
        )
        mme = RankedDevice(
            index=5, name="Microphone (Realtek)",
            host_api="MME", channels=1, sample_rate=48000,
            score=8.5, is_virtual=False,
        )
        ds = RankedDevice(
            index=9, name="Microphone (Realtek)",
            host_api="DirectSound", channels=1, sample_rate=48000,
            score=8.5, is_virtual=False,
        )
        result = deduplicate_devices([wasapi, mme, ds])
        assert len(result) == 1
        assert result[0].index == 1  # WASAPI won
        assert result[0].host_api == "WASAPI"

    def test_deduplication_preserves_different_devices(self) -> None:
        mic1 = RankedDevice(
            index=1, name="TONOR Mic",
            host_api="WASAPI", channels=1, sample_rate=48000,
            score=20.0, is_virtual=False,
        )
        mic2 = RankedDevice(
            index=3, name="Webcam Microphone",
            host_api="WASAPI", channels=1, sample_rate=44100,
            score=15.0, is_virtual=False,
        )
        result = deduplicate_devices([mic1, mic2])
        assert len(result) == 2

    def test_deduplication_empty_list(self) -> None:
        assert deduplicate_devices([]) == []


# ---------------------------------------------------------------------------
# Integration: list + detect (with mocked sounddevice)
# ---------------------------------------------------------------------------


class TestListRealDevices:
    def test_virtual_excluded(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = [
            _dev("Microphone (Realtek)", max_input_channels=2, hostapi=WASAPI),
            _dev("Stereo Mix (Realtek)", max_input_channels=2, hostapi=WASAPI),
            _dev("Loopback Device", max_input_channels=2, hostapi=MME),
        ]
        result = list_real_inputs()
        names = [d.name for d in result]
        assert "Microphone (Realtek)" in names
        assert "Stereo Mix (Realtek)" not in names
        assert "Loopback Device" not in names

    def test_output_virtual_excluded(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = [
            _dev("Speakers (Realtek)", max_output_channels=2, hostapi=WASAPI),
            _dev("CABLE Input (VB-Audio Virtual Cable)", max_output_channels=2, hostapi=WASAPI),
        ]
        result = list_real_outputs()
        assert len(result) == 1
        assert result[0].name == "Speakers (Realtek)"


class TestDetectBest:
    def test_detect_best_input_returns_non_virtual(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = [
            _dev("Stereo Mix (Realtek)", max_input_channels=2, hostapi=WASAPI),
            _dev("Microphone (USB Audio)", max_input_channels=1, hostapi=WASAPI),
            _dev("Microphone (USB Audio)", max_input_channels=1, hostapi=MME),
        ]
        best = detect_best_input()
        assert best is not None
        assert "Microphone" in best.name
        assert best.host_api == "Windows WASAPI"

    def test_detect_best_output(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = [
            _dev("Digital Audio (S/PDIF)", max_output_channels=2, hostapi=WASAPI),
            _dev("Speakers (Creative Pebble Pro)", max_output_channels=2, hostapi=WASAPI),
            _dev("Speakers (Creative Pebble Pro)", max_output_channels=2, hostapi=MME),
        ]
        best = detect_best_output()
        assert best is not None
        assert "Creative Pebble Pro" in best.name
        assert best.host_api == "Windows WASAPI"

    def test_detect_best_input_empty_returns_none(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = []
        assert detect_best_input() is None

    def test_detect_best_output_empty_returns_none(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = []
        assert detect_best_output() is None

    def test_detect_best_input_only_virtual_returns_none(self, patch_sd) -> None:
        """When all mics are virtual/loopback, detect_best_input returns None."""
        patch_sd.query_devices.return_value = [
            _dev("Stereo Mix (Realtek)", max_input_channels=2, hostapi=WASAPI),
            _dev("Loopback Device", max_input_channels=2, hostapi=MME),
            _dev("CABLE Output (VB-Audio)", max_input_channels=2, hostapi=WASAPI),
        ]
        assert detect_best_input() is None


# ---------------------------------------------------------------------------
# Realistic multi-device scenario (simulates the reporter's setup)
# ---------------------------------------------------------------------------


class TestRealisticScenario:
    """Simulates a typical Windows PC with 10+ enumerated entries but only
    one real USB mic and one real speaker pair."""

    DEVICES = [
        # WASAPI
        _dev("Microphone (TONOR TM20 Audio Device)", max_input_channels=1, hostapi=WASAPI, default_samplerate=48000),
        _dev("Stereo Mix (Realtek HD Audio)", max_input_channels=2, hostapi=WASAPI, default_samplerate=48000),
        _dev("Speakers (Realtek HD Audio)", max_output_channels=2, hostapi=WASAPI, default_samplerate=48000),
        _dev("Digital Audio (S/PDIF) (Realtek)", max_output_channels=2, hostapi=WASAPI, default_samplerate=48000),
        # MME duplicates
        _dev("Microphone (TONOR TM20 Audio Device)", max_input_channels=1, hostapi=MME, default_samplerate=48000),
        _dev("Stereo Mix (Realtek HD Audio)", max_input_channels=2, hostapi=MME, default_samplerate=48000),
        _dev("Speakers (Realtek HD Audio)", max_output_channels=2, hostapi=MME, default_samplerate=48000),
        # DirectSound duplicates
        _dev("Microphone (TONOR TM20 Audio Device)", max_input_channels=1, hostapi=DS, default_samplerate=48000),
        _dev("Stereo Mix (Realtek HD Audio)", max_input_channels=2, hostapi=DS, default_samplerate=48000),
        _dev("Speakers (Realtek HD Audio)", max_output_channels=2, hostapi=DS, default_samplerate=48000),
    ]

    def test_user_sees_one_mic(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = self.DEVICES
        inputs = list_real_inputs()
        # After filtering virtual (Stereo Mix) and deduplicating
        # (WASAPI wins over MME/DS), only the TONOR mic remains.
        assert len(inputs) == 1
        assert "TONOR" in inputs[0].name
        assert inputs[0].host_api == "Windows WASAPI"

    def test_user_sees_one_speaker(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = self.DEVICES
        outputs = list_real_outputs()
        # After filtering virtual (S/PDIF) and deduplicating, only
        # the Realtek Speakers remain.
        assert len(outputs) == 1
        assert "Speakers" in outputs[0].name

    def test_auto_detect_picks_tonor(self, patch_sd) -> None:
        patch_sd.query_devices.return_value = self.DEVICES
        best = detect_best_input()
        assert best is not None
        assert "TONOR" in best.name
