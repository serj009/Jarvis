"""Voice Registry: per-language voice profiles with easy swapping.

Architecture:
    voices/
        voice_registry.py   ← this file
    config:
        tts.voices.en = "en_GB-alan-medium"
        tts.voices.ru = "ru_RU-dmitri-medium"
        tts.voices.uk = "uk_UA-mykyta-medium"

Each language has its own voice assignment. When the detected language
changes, the TTS provider can switch to the appropriate voice without
reloading the whole model (Piper voices are fast to swap).

Future: when custom Baranov/Pecherytsya Piper models are trained,
just update the config — no code changes needed:
    tts.voices.ru = "ru_RU-baranov-custom"
    tts.voices.uk = "uk_UA-pecherytsya-custom"

For Qwen3-TTS (future GPU provider), the registry marks which voices
need GPU vs CPU, so hybrid_tts_manager can route correctly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class VoiceBackend(str, Enum):
    """Which TTS engine serves this voice."""
    PIPER = "piper"          # CPU, ~60MB per voice, 0 VRAM
    QWEN3_TTS = "qwen3_tts"  # GPU, ~2.5GB VRAM, voice cloning
    SYSTEM = "system"        # OS built-in TTS (fallback)


@dataclass(frozen=True)
class VoiceProfile:
    """A single voice configuration."""
    name: str                           # e.g. "en_GB-alan-medium"
    language: str                       # "en", "ru", "uk"
    backend: VoiceBackend = VoiceBackend.PIPER
    display_name: str = ""              # Human-readable, e.g. "Alan (British)"
    description: str = ""               # e.g. "Default English male voice"
    is_custom: bool = False             # True for user-trained voices
    reference_audio: str = ""           # Path to .wav for voice cloning (Qwen3-TTS)
    quality: str = "medium"             # "low", "medium", "high"
    sample_rate: int = 22050            # Expected output sample rate


# ---------------------------------------------------------------------------
# Built-in voice catalogue
# ---------------------------------------------------------------------------

# These are known Piper voices that can be downloaded from:
# https://github.com/rhasspy/piper/blob/master/VOICES.md

BUILTIN_VOICES: dict[str, VoiceProfile] = {
    # --- English ---
    "en_GB-alan-medium": VoiceProfile(
        name="en_GB-alan-medium",
        language="en",
        display_name="Alan (British)",
        description="Default English male voice. Clear, neutral British accent.",
    ),
    "en_US-lessac-medium": VoiceProfile(
        name="en_US-lessac-medium",
        language="en",
        display_name="Lessac (American)",
        description="American English male voice. Alternative to Alan.",
    ),

    # --- Russian ---
    "ru_RU-dmitri-medium": VoiceProfile(
        name="ru_RU-dmitri-medium",
        language="ru",
        display_name="Dmitri (Дмитрий)",
        description="Russian male voice. Clear pronunciation.",
    ),
    "ru_RU-irina-medium": VoiceProfile(
        name="ru_RU-irina-medium",
        language="ru",
        display_name="Irina (Ирина)",
        description="Russian female voice. Alternative to Dmitri.",
    ),

    # --- Ukrainian ---
    "uk_UA-mykyta-medium": VoiceProfile(
        name="uk_UA-mykyta-medium",
        language="uk",
        display_name="Mykyta (Микита)",
        description="Ukrainian male voice. MIT license. Recommended.",
    ),
    "uk_UA-lada-x_low": VoiceProfile(
        name="uk_UA-lada-x_low",
        language="uk",
        display_name="Lada (Лада)",
        description="Ukrainian female voice. Alternative to Mykyta. x_low quality.",
        quality="x_low",
    ),
}

# Placeholder entries for future custom trained voices
CUSTOM_VOICE_TEMPLATES: dict[str, VoiceProfile] = {
    "ru_RU-baranov-custom": VoiceProfile(
        name="ru_RU-baranov-custom",
        language="ru",
        display_name="Baranov (Баранов)",
        description="Custom Piper voice trained on Vyacheslav Baranov's "
                    "voice (Russian dub of JARVIS in Iron Man MCU).",
        is_custom=True,
        quality="medium",
    ),
    "uk_UA-pecherytsya-custom": VoiceProfile(
        name="uk_UA-pecherytsya-custom",
        language="uk",
        display_name="Pecherytsya (Печериця)",
        description="Custom Piper voice trained on Oleksandr Pecherytsya's "
                    "voice (Ukrainian dub of JARVIS in Iron Man 1-3).",
        is_custom=True,
        quality="medium",
    ),
    "en_US-baranov-cloned": VoiceProfile(
        name="en_US-baranov-cloned",
        language="en",
        backend=VoiceBackend.QWEN3_TTS,
        display_name="Baranov Clone (EN)",
        description="Baranov's timbre via Qwen3-TTS x_vector_only mode "
                    "(clean English pronunciation, Baranov's timbre).",
        is_custom=True,
        reference_audio="reference_voice/jarvis_baranov_sample1.wav",
    ),
}


# ---------------------------------------------------------------------------
# Voice Registry
# ---------------------------------------------------------------------------

class VoiceRegistry:
    """Manages available voices and per-language assignments.

    Usage:
        registry = VoiceRegistry(voices_dir=Path(".../voices"))
        registry.set_voice("ru", "ru_RU-dmitri-medium")
        voice = registry.get_voice("ru")
        # voice.name == "ru_RU-dmitri-medium"

        # Later, after training custom voice:
        registry.register(CUSTOM_VOICE_TEMPLATES["ru_RU-baranov-custom"])
        registry.set_voice("ru", "ru_RU-baranov-custom")
        # Done — JARVIS now speaks Russian with Baranov's voice
    """

    # Default voice assignments per language
    DEFAULTS: dict[str, str] = {
        "en": "en_GB-alan-medium",
        "ru": "ru_RU-dmitri-medium",
        "uk": "uk_UA-mykyta-medium",
    }

    def __init__(self, voices_dir: Path | None = None) -> None:
        self._voices_dir = voices_dir
        self._profiles: dict[str, VoiceProfile] = dict(BUILTIN_VOICES)
        self._assignments: dict[str, str] = dict(self.DEFAULTS)

    def register(self, profile: VoiceProfile) -> None:
        """Register a new voice profile (e.g. custom trained voice)."""
        self._profiles[profile.name] = profile
        log.info("registered voice: %s (%s)", profile.name, profile.display_name)

    def unregister(self, name: str) -> None:
        """Remove a voice profile."""
        self._profiles.pop(name, None)

    def set_voice(self, language: str, voice_name: str) -> None:
        """Assign a voice to a language."""
        if voice_name not in self._profiles:
            log.warning("voice %r not in registry, assigning anyway", voice_name)
        self._assignments[language] = voice_name

    def get_voice(self, language: str) -> VoiceProfile | None:
        """Get the assigned voice profile for a language."""
        name = self._assignments.get(language)
        if name is None:
            # Fallback to English
            name = self._assignments.get("en", "en_GB-alan-medium")
        return self._profiles.get(name)

    def get_voice_name(self, language: str) -> str:
        """Get just the voice name string for a language."""
        profile = self.get_voice(language)
        return profile.name if profile else self.DEFAULTS.get("en", "en_GB-alan-medium")

    def list_voices(self, language: str | None = None) -> list[VoiceProfile]:
        """List all registered voices, optionally filtered by language."""
        voices = list(self._profiles.values())
        if language:
            voices = [v for v in voices if v.language == language]
        return sorted(voices, key=lambda v: (v.language, v.name))

    def list_available(self, language: str | None = None) -> list[VoiceProfile]:
        """List voices that have .onnx files downloaded (Piper only)."""
        if self._voices_dir is None:
            return []
        available = []
        for profile in self.list_voices(language):
            if profile.backend != VoiceBackend.PIPER:
                continue
            onnx = self._voices_dir / f"{profile.name}.onnx"
            if onnx.exists():
                available.append(profile)
        return available

    @property
    def assignments(self) -> dict[str, str]:
        """Current language -> voice_name mapping."""
        return dict(self._assignments)

    def is_downloaded(self, voice_name: str) -> bool:
        """Check if a Piper voice model is downloaded."""
        if self._voices_dir is None:
            return False
        onnx = self._voices_dir / f"{voice_name}.onnx"
        config = self._voices_dir / f"{voice_name}.onnx.json"
        return onnx.exists() and config.exists()
