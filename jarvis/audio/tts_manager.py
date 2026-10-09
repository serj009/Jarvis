"""Multilingual TTS Manager: auto-switches voice by detected language.

Wraps PiperTTS and transparently switches voice when the language changes.
When Qwen3-TTS is available (future), routes GPU-capable languages to it
and falls back to Piper for the rest.

Architecture:
    Pipeline detects language (STT) → passes to tts_manager.speak()
    → tts_manager selects voice for that language → PiperTTS.speak()

Voice switching is fast: Piper reloads a new .onnx model (~200ms).
Switching happens only when the language actually changes.

Usage:
    manager = TTSManager(
        piper_tts=piper,
        voice_registry=registry,
    )
    # Speaks with Russian voice
    await manager.speak("Привет, сэр", language="ru")
    # Switches to Ukrainian voice
    await manager.speak("Привіт, сер", language="uk")
    # Switches to English voice
    await manager.speak("Hello, sir", language="en")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.audio.tts import PiperTTS
    from jarvis.audio.voice_registry import VoiceRegistry

log = logging.getLogger(__name__)


class TTSManager:
    """Manages multilingual TTS with automatic voice switching.

    Currently wraps PiperTTS only. Future: add Qwen3-TTS as a second
    backend for GPU voice cloning (EN/RU) while keeping Piper for
    Ukrainian and fallback.

    The manager tracks the current active voice and only reloads when
    the language changes. Reloading a Piper voice takes ~200ms — fast
    enough to be imperceptible between STT and TTS in the pipeline.
    """

    def __init__(
        self,
        *,
        piper_tts: PiperTTS,
        voice_registry: VoiceRegistry,
        default_language: str = "en",
    ) -> None:
        self._piper = piper_tts
        self._registry = voice_registry
        self._current_language = default_language
        self._current_voice_name = piper_tts.voice_name

    @property
    def current_language(self) -> str:
        return self._current_language

    @property
    def current_voice(self) -> str:
        return self._current_voice_name

    async def set_language(self, language: str) -> None:
        """Pre-set language so next speak() uses the right voice.

        Call this from the pipeline after STT detects language,
        before any TTS speak() calls in that turn."""
        if language and language != self._current_language:
            await self._switch_voice(language)

    async def speak(self, text: str, *, language: str | None = None) -> None:
        """Speak text, switching voice if language changed."""
        if language and language != self._current_language:
            await self._switch_voice(language)
        await self._piper.speak(text)

    async def speak_stream(
        self, text_chunks: AsyncIterator[str], *, language: str | None = None
    ) -> None:
        """Speak streaming text, switching voice if language changed."""
        if language and language != self._current_language:
            await self._switch_voice(language)
        await self._piper.speak_stream(text_chunks)

    async def cancel(self) -> None:
        """Cancel current playback."""
        await self._piper.cancel()

    async def _switch_voice(self, language: str) -> None:
        """Switch to the voice assigned to the given language."""
        target_name = self._registry.get_voice_name(language)

        if target_name == self._current_voice_name:
            self._current_language = language
            return

        # Check if voice model is downloaded
        if not self._registry.is_downloaded(target_name):
            log.warning(
                "voice %r for language %r not downloaded, keeping %r",
                target_name, language, self._current_voice_name,
            )
            self._current_language = language
            return

        log.info(
            "switching voice: %r → %r (language: %s → %s)",
            self._current_voice_name, target_name,
            self._current_language, language,
        )

        # Unload current voice and load new one
        try:
            if self._piper.is_loaded:
                await self._piper.unload()
            self._piper.voice_name = target_name
            await self._piper.load()
            self._current_voice_name = target_name
            self._current_language = language
        except Exception:
            log.error(
                "failed to switch to voice %r, keeping %r",
                target_name, self._current_voice_name,
                exc_info=True,
            )

    # -- Convenience: get available voices for UI/settings --

    def available_voices(self, language: str | None = None) -> list[dict]:
        """List available voices for settings UI."""
        voices = self._registry.list_voices(language)
        return [
            {
                "name": v.name,
                "language": v.language,
                "display_name": v.display_name or v.name,
                "description": v.description,
                "is_custom": v.is_custom,
                "downloaded": self._registry.is_downloaded(v.name),
                "active": v.name == self._current_voice_name,
            }
            for v in voices
        ]
