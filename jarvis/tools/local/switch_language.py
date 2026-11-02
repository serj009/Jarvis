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

from jarvis.core.phrases import Lang
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


# ---------------------------------------------------------------------------
# Free-form "speak <language>" commands
# ---------------------------------------------------------------------------
# The short anchored patterns below only caught the exact phrasings. Live
# 2026-11-02 a 43-char "говори українською ..." variant missed them, went to
# the LLM, got a Ukrainian reply -- and the language was NOT locked, so the
# next question was auto-detected as Russian again. These patterns accept a
# speak/switch verb, a few filler words, then the language; they are anchored
# at both ends so "говори українською, яке в мене місто" (a question tacked
# on) still goes to the LLM rather than being swallowed.

_VERB = (
    r"(?:давай(?:те)?\s+)?"
    r"(?:говори(?:ти|те)?|говорить|розмовляй(?:те)?|розмовляти"
    r"|спілкуйся|спілкуватися|відповідай(?:те)?|відповідати"
    r"|разговаривай(?:те)?|разговаривать|отвечай(?:те)?|отвечать"
    r"|общайся|общаться|перейди|перейдіть|перейдем|перейдемо"
    r"|переключи(?:сь|ся)?|переключіться|переключитесь)"
)
_FILLER = (
    r"(?:[\s,]+(?:будь[\s-]+ласка|пожалуйста|зі\s+мною|со\s+мной|мені|мне"
    r"|тепер|теперь|далі|дальше|відтепер|отныне|тільки|лише|только))*"
)
_TAIL = r"(?:[\s,]+(?:будь[\s-]+ласка|пожалуйста|мовою|мову|языком|язык|please))*$"
_LANG_WORDS: dict[str, str] = {
    "uk": (
        r"(?:українською|по[- ]?українськи|на\s+українськ\w*"
        r"|по[- ]?украински|на\s+украинск\w*|українська|украинский)"
    ),
    "ru": (
        r"(?:російською|по[- ]?російськи|на\s+російськ\w*"
        r"|по[- ]?русски|на\s+русск\w*|російська|русский)"
    ),
    "en": (
        r"(?:англійською|по[- ]?англійськи|на\s+англійськ\w*"
        r"|по[- ]?английски|на\s+английск\w*|англійська|английский)"
    ),
}
_EN_NAMES: dict[str, str] = {"uk": "ukrainian", "ru": "russian", "en": "english"}


def _free_form_patterns() -> tuple[VoicePattern, ...]:
    out: list[VoicePattern] = []
    for lang, words in _LANG_WORDS.items():
        out.append(VoicePattern(
            regex=rf"^{_VERB}{_FILLER}[\s,]+{words}{_TAIL}",
            priority=10,
            args=lambda m, _lang=lang: {"language": _lang},
        ))
        out.append(VoicePattern(
            regex=(
                rf"^(?:please\s+)?(?:speak|talk|reply|answer|switch\s+to)"
                rf"(?:\s+to\s+me)?(?:\s+in)?\s+{_EN_NAMES[lang]}{_TAIL}"
            ),
            priority=10,
            args=lambda m, _lang=lang: {"language": _lang},
        ))
    return tuple(out)


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
    ) + _free_form_patterns()

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
