"""Switch the language Jarvis listens and responds in.

Voice-routed: "speak english", "говори по-русски", "говори українською"
bypass the LLM entirely and lock STT + TTS to the requested language.

"auto" re-enables Whisper auto-detection so the user can mix languages
freely again.

Architecture:
    SwitchLanguageTool.execute()
        -> sets stt.language (Whisper forced language)
        -> calls tts_manager.set_language() (voice switch)
        -> returns spoken confirmation in the NEW language
"""

from __future__ import annotations

import logging
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.core.phrases import Lang, say
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


class SwitchLanguageArgs(BaseModel):
    language: Literal["en", "ru", "uk", "auto"] = Field(
        description=(
            "Target language code: 'en' for English, 'ru' for Russian, "
            "'uk' for Ukrainian, 'auto' for auto-detection."
        ),
    )


# Confirmation phrases (in the TARGET language so user hears it correctly).
_CONFIRMATIONS: dict[str, dict[Lang, str]] = {
    "en": {
        "en": "Switched to English, sir.",
        "ru": "Switched to English, sir.",
        "uk": "Switched to English, sir.",
    },
    "ru": {
        "en": "Переключился на русский, сэр.",
        "ru": "Переключился на русский, сэр.",
        "uk": "Переключився на російську, сер.",
    },
    "uk": {
        "en": "Переключився на українську, сер.",
        "ru": "Переключился на украинский, сэр.",
        "uk": "Переключився на українську, сер.",
    },
    "auto": {
        "en": "Auto-detection enabled, sir.",
        "ru": "Автоопределение включено, сэр.",
        "uk": "Автовизначення увімкнено, сер.",
    },
}


class SwitchLanguageTool:
    """Switch Jarvis listening/speaking language via voice command."""

    name: str = "switch_language"
    description: str = (
        "Switch the language Jarvis listens in and responds with. "
        "Accepts 'en', 'ru', 'uk', or 'auto' for auto-detection."
    )
    args_schema = SwitchLanguageArgs
    requires_confirmation: bool = False

    # Voice patterns: first-match-wins, bypass the LLM.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # English commands
        VoicePattern(
            regex=r"^(?:speak|switch to|in)\s+english",
            priority=10,
            args=lambda m: {"language": "en"},
        ),
        VoicePattern(
            regex=r"^english\s+(?:please|mode)",
            priority=10,
            args=lambda m: {"language": "en"},
        ),
        # Russian commands
        VoicePattern(
            regex=r"^говори\s+по[- ]?русски",
            priority=10,
            args=lambda m: {"language": "ru"},
        ),
        VoicePattern(
            regex=r"^переключи(?:сь)?\s+на\s+русский",
            priority=10,
            args=lambda m: {"language": "ru"},
        ),
        VoicePattern(
            regex=r"^(?:на\s+)?русском",
            priority=10,
            args=lambda m: {"language": "ru"},
        ),
        # Ukrainian commands
        VoicePattern(
            regex=r"^говори\s+українською",
            priority=10,
            args=lambda m: {"language": "uk"},
        ),
        VoicePattern(
            regex=r"^переключи(?:сь)?\s+на\s+українськ",
            priority=10,
            args=lambda m: {"language": "uk"},
        ),
        VoicePattern(
            regex=r"^(?:по[- ])?українськи",
            priority=10,
            args=lambda m: {"language": "uk"},
        ),
        # Auto-detect
        VoicePattern(
            regex=r"^auto(?:\s+detect)?(?:\s+language)?",
            priority=10,
            args=lambda m: {"language": "auto"},
        ),
        VoicePattern(
            regex=r"^автоопределение",
            priority=10,
            args=lambda m: {"language": "auto"},
        ),
        VoicePattern(
            regex=r"^автовизначення",
            priority=10,
            args=lambda m: {"language": "auto"},
        ),
    )

    def __init__(
        self,
        *,
        stt: object | None = None,
        tts_manager: object | None = None,
    ) -> None:
        """
        Args:
            stt: FasterWhisperSTT instance — to set forced language.
            tts_manager: TTSManager instance — to switch voice.
        """
        self._stt = stt
        self._tts_manager = tts_manager

    async def execute(self, args: SwitchLanguageArgs) -> ToolResult:
        target = args.language
        log.info("switching language to %r", target)

        errors: list[str] = []

        # 1. Switch STT language (Whisper forced mode).
        if self._stt is not None:
            try:
                self._stt.language = target
                # Update hotwords for the new language.
                from jarvis.audio.stt import HOTWORDS_BY_LANG
                self._stt.hotwords = HOTWORDS_BY_LANG.get(target, "")
                log.info("STT language set to %r", target)
            except Exception as e:
                log.error("failed to set STT language: %s", e)
                errors.append(f"STT: {e}")
        else:
            log.warning("no STT instance provided; skipping STT switch")

        # 2. Switch TTS voice via TTSManager.
        if self._tts_manager is not None and target != "auto":
            try:
                await self._tts_manager.set_language(target)
                log.info("TTS voice switched to %r", target)
            except Exception as e:
                log.error("failed to switch TTS voice: %s", e)
                errors.append(f"TTS: {e}")

        if errors:
            return ToolResult(
                success=False,
                error=f"Partial switch: {'; '.join(errors)}",
            )

        # Confirm in the TARGET language.
        from jarvis.core.phrases import reply_language
        current_lang = reply_language()
        confirmation = _CONFIRMATIONS.get(target, {}).get(
            current_lang, f"Switched to {target}."
        )

        return ToolResult(success=True, output=confirmation)
