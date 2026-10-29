"""Short synthesised notification sounds for auditory feedback.

Tones are generated programmatically via *numpy* + *sounddevice* so no
external WAV assets are needed.  Both libraries are imported lazily on
first play; if either is missing the class falls back silently (every
play_* call becomes a no-op) so the rest of Jarvis keeps working on
machines without audio dependencies.

Tone palette
~~~~~~~~~~~~
* **wake**    — 880 Hz sine, 100 ms (confirmation beep)
* **success** — two-tone ding 1200→1500 Hz, 150 ms
* **error**   — 300 Hz sine, 200 ms (low warning)
* **timer**   — three 880 Hz pips, 100 ms each, 60 ms gap

All waveforms are rendered at 44 100 Hz / float32 / mono and cached
after first generation so repeated calls are allocation-free.

Thread safety: ``sounddevice.play`` is documented as thread-safe and
internally acquires a lock, so callers may invoke play_* from any
thread (audio loop, Qt thread, etc.) without external synchronisation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # numpy typing only needed for stubs

log = logging.getLogger(__name__)

# Playback sample rate — 44.1 kHz, CD quality, universally supported.
_RATE: int = 44_100


def _lazy_imports() -> tuple:
    """Return ``(numpy, sounddevice)`` or raise *ImportError*."""
    import numpy as np  # noqa: WPS433 — lazy by design
    import sounddevice as sd  # noqa: WPS433
    return np, sd


class SoundEffects:
    """Play short notification sounds.

    Uses synthesised tones (no external WAV files needed).
    Falls back silently if *sounddevice* or *numpy* are not available.
    """

    def __init__(self, enabled: bool = True, volume: float = 0.3) -> None:
        self._enabled = enabled
        self._volume = max(0.0, min(1.0, volume))
        self._available: bool | None = None  # resolved on first play
        self._np = None
        self._sd = None
        # Cached waveform arrays (generated once, replayed many times).
        self._cache: dict[str, object] = {}

    # -- public API ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, value))
        # Invalidate cache so next play re-generates with new volume.
        self._cache.clear()

    def play_wake(self) -> None:
        """Short high beep — wake word detected."""
        self._play("wake")

    def play_success(self) -> None:
        """Short pleasant ding — action completed."""
        self._play("success")

    def play_error(self) -> None:
        """Short low tone — error occurred."""
        self._play("error")

    def play_timer(self) -> None:
        """Bell sound — timer/reminder expired."""
        self._play("timer")

    # -- internals ----------------------------------------------------------

    def _ensure_imports(self) -> bool:
        """Resolve lazy imports once; return True if playback is possible."""
        if self._available is not None:
            return self._available
        try:
            self._np, self._sd = _lazy_imports()
            self._available = True
        except ImportError:
            log.info(
                "sounddevice/numpy not available — sound effects disabled"
            )
            self._available = False
        return self._available

    def _play(self, name: str) -> None:
        if not self._enabled:
            return
        if not self._ensure_imports():
            return
        try:
            waveform = self._cache.get(name)
            if waveform is None:
                generator = _GENERATORS.get(name)
                if generator is None:
                    return
                waveform = generator(self._np, self._volume)
                self._cache[name] = waveform
            self._sd.play(waveform, samplerate=_RATE)
        except Exception:
            log.warning("sound effect %r playback failed", name, exc_info=True)

    # -- static cache-key check for tests -----------------------------------

    @staticmethod
    def known_effects() -> frozenset[str]:
        """Return the set of recognised effect names."""
        return frozenset(_GENERATORS)


# ---------------------------------------------------------------------------
# Waveform generators — pure numpy, no side effects.
# Each returns an ndarray[float32] ready for sounddevice.play().
# ---------------------------------------------------------------------------


def _gen_wake(np, volume: float):
    """880 Hz sine, 100 ms — short confirmation beep."""
    t = np.linspace(0, 0.1, int(_RATE * 0.1), endpoint=False, dtype=np.float32)
    tone = np.sin(2 * np.pi * 880 * t).astype(np.float32)
    # Apply a 5 ms fade-in/out to avoid clicks.
    _apply_fade(np, tone, int(_RATE * 0.005))
    return tone * volume


def _gen_success(np, volume: float):
    """Two-tone ding: 1200 Hz → 1500 Hz, 150 ms total."""
    half = int(_RATE * 0.075)
    t1 = np.linspace(0, 0.075, half, endpoint=False, dtype=np.float32)
    t2 = np.linspace(0, 0.075, half, endpoint=False, dtype=np.float32)
    tone = np.concatenate([
        np.sin(2 * np.pi * 1200 * t1),
        np.sin(2 * np.pi * 1500 * t2),
    ]).astype(np.float32)
    _apply_fade(np, tone, int(_RATE * 0.005))
    return tone * volume


def _gen_error(np, volume: float):
    """300 Hz sine, 200 ms — low warning tone."""
    t = np.linspace(0, 0.2, int(_RATE * 0.2), endpoint=False, dtype=np.float32)
    tone = np.sin(2 * np.pi * 300 * t).astype(np.float32)
    _apply_fade(np, tone, int(_RATE * 0.005))
    return tone * volume


def _gen_timer(np, volume: float):
    """Three 880 Hz pips, 100 ms each, 60 ms silence between them."""
    pip_samples = int(_RATE * 0.1)
    gap_samples = int(_RATE * 0.06)
    t_pip = np.linspace(0, 0.1, pip_samples, endpoint=False, dtype=np.float32)
    pip = np.sin(2 * np.pi * 880 * t_pip).astype(np.float32)
    _apply_fade(np, pip, int(_RATE * 0.005))
    gap = np.zeros(gap_samples, dtype=np.float32)
    tone = np.concatenate([pip, gap, pip, gap, pip]).astype(np.float32)
    return tone * volume


def _apply_fade(np, samples, fade_len: int) -> None:
    """In-place linear fade-in/out to avoid audible clicks."""
    if fade_len <= 0 or len(samples) < 2 * fade_len:
        return
    ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    samples[:fade_len] *= ramp
    samples[-fade_len:] *= ramp[::-1]


_GENERATORS: dict[str, object] = {
    "wake": _gen_wake,
    "success": _gen_success,
    "error": _gen_error,
    "timer": _gen_timer,
}
