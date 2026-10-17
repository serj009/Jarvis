"""Tests for jarvis.audio.sound_effects — synthesised notification tones.

All tests mock *sounddevice* and *numpy* so they run on any machine
without audio hardware or optional dependencies installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.audio.sound_effects import SoundEffects, _GENERATORS, _RATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sfx(**kwargs) -> SoundEffects:
    """Create a SoundEffects instance with dependencies available."""
    return SoundEffects(**kwargs)


@pytest.fixture()
def mock_sd():
    """Patch sounddevice so no real audio device is needed."""
    sd = MagicMock()
    with patch("jarvis.audio.sound_effects._lazy_imports") as lazy:
        import numpy as _np_real
        lazy.return_value = (_np_real, sd)
        yield sd


# ---------------------------------------------------------------------------
# 1. Construction defaults
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_enabled(self) -> None:
        sfx = SoundEffects()
        assert sfx.enabled is True

    def test_default_volume(self) -> None:
        sfx = SoundEffects()
        assert sfx.volume == pytest.approx(0.3)

    def test_custom_volume_clamped_high(self) -> None:
        sfx = SoundEffects(volume=5.0)
        assert sfx.volume == pytest.approx(1.0)

    def test_custom_volume_clamped_low(self) -> None:
        sfx = SoundEffects(volume=-1.0)
        assert sfx.volume == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2. Enabled / disabled gating
# ---------------------------------------------------------------------------

class TestEnabledGating:
    def test_disabled_skips_play(self, mock_sd: MagicMock) -> None:
        sfx = _make_sfx(enabled=False)
        sfx.play_wake()
        mock_sd.play.assert_not_called()

    def test_enabled_plays(self, mock_sd: MagicMock) -> None:
        sfx = _make_sfx(enabled=True)
        sfx.play_wake()
        mock_sd.play.assert_called_once()

    def test_runtime_toggle(self, mock_sd: MagicMock) -> None:
        sfx = _make_sfx(enabled=True)
        sfx.enabled = False
        sfx.play_wake()
        mock_sd.play.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Graceful fallback when numpy/sounddevice missing
# ---------------------------------------------------------------------------

class TestGracefulFallback:
    def test_import_failure_noop(self) -> None:
        """When sounddevice is not installed, play_* must not raise."""
        with patch(
            "jarvis.audio.sound_effects._lazy_imports",
            side_effect=ImportError("no sounddevice"),
        ):
            sfx = SoundEffects()
            sfx.play_wake()    # must not raise
            sfx.play_success()
            sfx.play_error()
            sfx.play_timer()

    def test_import_checked_once(self) -> None:
        """Lazy import is resolved once; subsequent plays skip the check."""
        with patch(
            "jarvis.audio.sound_effects._lazy_imports",
            side_effect=ImportError("nope"),
        ) as mock_imp:
            sfx = SoundEffects()
            sfx.play_wake()
            sfx.play_wake()
            assert mock_imp.call_count == 1


# ---------------------------------------------------------------------------
# 4. Each effect triggers sounddevice.play with correct sample rate
# ---------------------------------------------------------------------------

class TestEffects:
    @pytest.mark.parametrize("method", ["play_wake", "play_success", "play_error", "play_timer"])
    def test_play_calls_sd(self, mock_sd: MagicMock, method: str) -> None:
        sfx = _make_sfx()
        getattr(sfx, method)()
        mock_sd.play.assert_called_once()
        _, kwargs = mock_sd.play.call_args
        assert kwargs["samplerate"] == _RATE


# ---------------------------------------------------------------------------
# 5. Waveform caching — second call reuses cached array
# ---------------------------------------------------------------------------

class TestCaching:
    def test_second_call_uses_cache(self, mock_sd: MagicMock) -> None:
        sfx = _make_sfx()
        sfx.play_wake()
        sfx.play_wake()
        assert mock_sd.play.call_count == 2
        arr1 = mock_sd.play.call_args_list[0][0][0]
        arr2 = mock_sd.play.call_args_list[1][0][0]
        assert arr1 is arr2  # exact same object (cached)

    def test_volume_change_invalidates_cache(self, mock_sd: MagicMock) -> None:
        sfx = _make_sfx(volume=0.3)
        sfx.play_wake()
        arr1 = mock_sd.play.call_args_list[0][0][0]
        sfx.volume = 0.8
        sfx.play_wake()
        arr2 = mock_sd.play.call_args_list[1][0][0]
        assert arr1 is not arr2


# ---------------------------------------------------------------------------
# 6. Volume affects amplitude
# ---------------------------------------------------------------------------

class TestVolume:
    def test_louder_volume_larger_amplitude(self, mock_sd: MagicMock) -> None:
        sfx_quiet = _make_sfx(volume=0.1)
        sfx_quiet.play_wake()
        arr_quiet = mock_sd.play.call_args_list[0][0][0]

        sfx_loud = _make_sfx(volume=0.9)
        sfx_loud.play_wake()
        arr_loud = mock_sd.play.call_args_list[1][0][0]

        import numpy as np
        assert np.max(np.abs(arr_loud)) > np.max(np.abs(arr_quiet))


# ---------------------------------------------------------------------------
# 7. known_effects covers all generators
# ---------------------------------------------------------------------------

class TestKnownEffects:
    def test_matches_generators(self) -> None:
        assert SoundEffects.known_effects() == frozenset(_GENERATORS)

    def test_all_four_present(self) -> None:
        expected = {"wake", "success", "error", "timer"}
        assert expected == set(SoundEffects.known_effects())


# ---------------------------------------------------------------------------
# 8. Playback exception is swallowed (no crash)
# ---------------------------------------------------------------------------

class TestPlaybackException:
    def test_sd_play_exception_swallowed(self, mock_sd: MagicMock) -> None:
        mock_sd.play.side_effect = RuntimeError("device error")
        sfx = _make_sfx()
        sfx.play_wake()  # must not raise
