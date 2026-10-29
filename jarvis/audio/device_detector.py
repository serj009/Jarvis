"""Smart audio device detection.

Filters and ranks audio devices for voice assistant use:
1. Prefer WASAPI over MME/DirectSound (lower latency, better quality)
2. Filter out virtual/loopback devices (Stereo Mix, What U Hear, etc.)
3. Rank real microphones by input channels + sample rate
4. Rank real speakers by output channels
5. Auto-detect best microphone and speaker on first run

Design:
- Called at first launch when config has no explicit device
- Called when Settings → "Auto-detect" button is pressed
- Results saved to config so they persist

Virtual device blacklist (case-insensitive substrings):
  "stereo mix", "what u hear", "wave out", "loopback", "virtual",
  "vb-audio", "cable", "voicemeeter", "nvidia broadcast",
  "remote audio", "steam streaming"

Physical device indicators:
  "usb", "microphone", "headset", "webcam", "realtek",
  "high definition audio", "speakers", "headphones"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blacklist / whitelist patterns (all compared lower-cased)
# ---------------------------------------------------------------------------

# Virtual/loopback devices to exclude from user-facing lists.
VIRTUAL_DEVICE_PATTERNS: set[str] = {
    "stereo mix",
    "what u hear",
    "wave out mix",
    "loopback",
    "virtual",
    "vb-audio",
    "cable input",
    "cable output",
    "voicemeeter",
    "nvidia broadcast",
    "nvidia rtx voice",
    "remote audio",
    "steam streaming",
    "digital audio",
    "s/pdif",
    "spdif",
}

# Patterns that indicate a real physical input (microphone).
PHYSICAL_INPUT_PATTERNS: set[str] = {
    "microphone",
    "mic",
    "usb",
    "headset",
    "webcam",
    "camera",
    "realtek",
    "high definition",
    "tonor",
    "blue",
    "rode",
    "samson",
    "audio-technica",
    "hyperx",
    "razer",
    "logitech",
    "corsair",
    "steelseries",
}

# Patterns that indicate real physical output (speakers/headphones).
PHYSICAL_OUTPUT_PATTERNS: set[str] = {
    "speakers",
    "headphones",
    "headset",
    "realtek",
    "high definition",
    "usb",
    "pebble",
    "creative",
    "logitech",
    "razer",
    "corsair",
    "steelseries",
    "jbl",
    "sony",
    "sennheiser",
    "bose",
    "audio-technica",
}


@dataclass
class RankedDevice:
    """A device with a human-friendly label and quality score."""

    index: int
    name: str
    host_api: str  # "WASAPI", "MME", "DirectSound", etc.
    channels: int
    sample_rate: int
    score: float  # higher = better
    is_virtual: bool

    @property
    def clean_name(self) -> str:
        """Name without host-API suffix — suitable for user-facing display."""
        return self.name


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _is_virtual(name: str) -> bool:
    """Check if device name matches virtual/loopback patterns."""
    lower = name.lower()
    return any(pattern in lower for pattern in VIRTUAL_DEVICE_PATTERNS)


def _score_input_device(
    name: str, host_api: str, channels: int, rate: int,
) -> float:
    """Score an input device. Higher = more likely a real mic."""
    score = 0.0
    lower = name.lower()

    # WASAPI preferred (lower latency, no stream-restart race).
    if "wasapi" in host_api.lower():
        score += 10.0

    # Physical mic indicators.
    if any(p in lower for p in PHYSICAL_INPUT_PATTERNS):
        score += 5.0

    # USB mics tend to be dedicated (not built-in chipset noise).
    if "usb" in lower:
        score += 3.0

    # Mono/stereo mic (not surround — that's usually virtual).
    if 1 <= channels <= 2:
        score += 2.0

    # Higher sample rate = better quality.
    if rate >= 44100:
        score += 1.0
    if rate >= 48000:
        score += 0.5

    return score


def _score_output_device(
    name: str, host_api: str, channels: int, rate: int,
) -> float:
    """Score an output device. Higher = more likely real speakers."""
    score = 0.0
    lower = name.lower()

    if "wasapi" in host_api.lower():
        score += 10.0

    if any(p in lower for p in PHYSICAL_OUTPUT_PATTERNS):
        score += 5.0

    # Stereo output preferred for TTS.
    if channels >= 2:
        score += 2.0

    if rate >= 44100:
        score += 1.0

    return score


# ---------------------------------------------------------------------------
# Low-level enumeration (thin wrapper over sounddevice)
# ---------------------------------------------------------------------------


def _enumerate_raw(kind: str) -> list[RankedDevice]:
    """Query sounddevice and return every device of `kind` ('input'|'output')
    wrapped as a RankedDevice with scores pre-computed.

    Returns an empty list on any sounddevice error so callers degrade
    gracefully when no audio backend is present.
    """
    try:
        assert sd is not None, "sounddevice not installed"
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        log.warning("could not enumerate %s devices: %s", kind, exc)
        return []

    ch_field = (
        "max_input_channels" if kind == "input" else "max_output_channels"
    )
    scorer = _score_input_device if kind == "input" else _score_output_device
    result: list[RankedDevice] = []

    for idx, dev in enumerate(devices):
        channels = int(dev.get(ch_field, 0))
        if channels <= 0:
            continue

        name = str(dev.get("name", ""))
        ha_index = dev.get("hostapi")
        ha_name = ""
        if isinstance(ha_index, int) and 0 <= ha_index < len(hostapis):
            ha_name = str(hostapis[ha_index].get("name", ""))

        rate = int(dev.get("default_samplerate", 0) or 0)
        virtual = _is_virtual(name)
        score = scorer(name, ha_name, channels, rate)
        # Virtual devices get a large score penalty so they always sort last.
        if virtual:
            score -= 100.0

        result.append(
            RankedDevice(
                index=idx,
                name=name,
                host_api=ha_name,
                channels=channels,
                sample_rate=rate,
                score=score,
                is_virtual=virtual,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _canonical_key(name: str) -> str:
    """Strip host-API bracketed suffix and normalise whitespace so
    'Microphone (Realtek) [WASAPI]' and 'Microphone (Realtek) [MME]'
    collapse to the same canonical key."""
    import re

    # Remove trailing "[WASAPI]", "[MME]", "[Windows DirectSound]", etc.
    cleaned = re.sub(r"\s*\[.*?\]\s*$", "", name).strip()
    # Collapse whitespace.
    return re.sub(r"\s+", " ", cleaned).lower()


def deduplicate_devices(
    devices: list[RankedDevice],
) -> list[RankedDevice]:
    """Remove MME/DirectSound duplicates when a WASAPI variant exists.

    Keep only the highest-scored variant per physical device.  The
    canonical key is the device name stripped of its host-API suffix so
    that "Microphone (Realtek) [WASAPI]" and "Microphone (Realtek) [MME]"
    are treated as duplicates.
    """
    best: dict[str, RankedDevice] = {}
    for dev in devices:
        key = _canonical_key(dev.name)
        existing = best.get(key)
        if existing is None or dev.score > existing.score:
            best[key] = dev
    # Preserve the original ordering (highest score first) rather than
    # dict-insertion order.
    deduped = list(best.values())
    deduped.sort(key=lambda d: d.score, reverse=True)
    return deduped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_real_inputs() -> list[RankedDevice]:
    """List input devices, filtered and ranked.

    Virtual devices are excluded. Duplicates across host APIs (WASAPI,
    MME, DirectSound) are collapsed — the WASAPI variant wins.
    Sorted by score descending.
    """
    raw = _enumerate_raw("input")
    real = [d for d in raw if not d.is_virtual]
    return deduplicate_devices(real)


def list_real_outputs() -> list[RankedDevice]:
    """List output devices, filtered and ranked.

    Virtual devices are excluded. Duplicates across host APIs collapsed.
    Sorted by score descending.
    """
    raw = _enumerate_raw("output")
    real = [d for d in raw if not d.is_virtual]
    return deduplicate_devices(real)


def detect_best_input() -> RankedDevice | None:
    """Auto-detect the best microphone.

    Returns the highest-scored non-virtual input device, or None when no
    usable microphone is found.
    """
    candidates = list_real_inputs()
    if not candidates:
        log.warning("auto-detect: no usable input (microphone) device found")
        return None
    best = candidates[0]
    log.info(
        "auto-detect: best input → %r (idx=%d, api=%s, score=%.1f)",
        best.name,
        best.index,
        best.host_api,
        best.score,
    )
    return best


def detect_best_output() -> RankedDevice | None:
    """Auto-detect the best speaker/headphones.

    Returns the highest-scored non-virtual output device, or None when no
    usable speaker is found.
    """
    candidates = list_real_outputs()
    if not candidates:
        log.warning("auto-detect: no usable output (speaker) device found")
        return None
    best = candidates[0]
    log.info(
        "auto-detect: best output → %r (idx=%d, api=%s, score=%.1f)",
        best.name,
        best.index,
        best.host_api,
        best.score,
    )
    return best
