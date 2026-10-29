"""Voice-based confirmation for CodingAgent destructive actions.

When the agent wants to delete a file, overwrite an existing file, commit,
push, run an arbitrary shell command, or install packages, it asks the user
via TTS and waits for a voice response.

Positive responses (EN/RU/UK):
  "yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "go ahead",
  "да", "ок", "подтверждаю", "давай", "конечно", "хорошо",
  "так", "підтверджую", "добре", "гаразд"

Negative responses:
  "no", "nope", "cancel", "stop", "don't", "abort",
  "нет", "отмена", "стоп", "не надо", "не нужно",
  "ні", "скасуй", "не треба", "зупини"

Timeout: 15 seconds → treated as "no" (safe default).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

_POSITIVE = re.compile(
    r"^(?:yes|yeah|yep|sure|ok|okay|confirm|go ahead"
    r"|да|ок|подтвер\w*|давай|конечно|хорошо"
    r"|так|підтвер\w*|добре|гаразд)$",
    re.IGNORECASE,
)

_NEGATIVE = re.compile(
    r"^(?:no|nope|cancel|stop|don't|abort"
    r"|нет|отмен\w*|стоп|не надо|не нужно"
    r"|ні|скасу\w*|не треба|зупини)$",
    re.IGNORECASE,
)

# Languages supported for confirmation prompts.
SUPPORTED_LANGUAGES = ("en", "ru", "uk")

# Default timeouts (seconds).
_FIRST_LISTEN_TIMEOUT: float = 15.0
_RETRY_LISTEN_TIMEOUT: float = 10.0


def is_positive(text: str) -> bool:
    """Return True if *text* matches a known positive response."""
    return bool(_POSITIVE.match(text.strip().lower()))


def is_negative(text: str) -> bool:
    """Return True if *text* matches a known negative response."""
    return bool(_NEGATIVE.match(text.strip().lower()))


class VoiceConfirm:
    """Ask user for voice confirmation of a destructive action.

    Integration:
    - Needs a TTS callback to speak the question.
    - Needs an STT callback to listen for the response.
    - Or: uses pipeline bus events.

    When *speak_fn* or *listen_fn* is ``None`` the instance is effectively
    disabled — :meth:`confirm` always returns ``True`` (no confirmation
    needed, backward-compatible).
    """

    def __init__(
        self,
        speak_fn: Callable[[str], Awaitable[None]] | None,
        listen_fn: Callable[[float], Awaitable[str | None]] | None,
    ) -> None:
        """
        Parameters
        ----------
        speak_fn:
            ``async (text: str) -> None`` — speak through TTS.
        listen_fn:
            ``async (timeout: float) -> str | None`` — listen for voice,
            return transcription or ``None`` on timeout.
        """
        self._speak = speak_fn
        self._listen = listen_fn

    # -- public API --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """``True`` when both speak and listen callbacks are wired."""
        return self._speak is not None and self._listen is not None

    async def confirm(
        self,
        action_description: str,
        language: str = "en",
    ) -> bool:
        """Ask for voice confirmation, return ``True`` if confirmed.

        Parameters
        ----------
        action_description:
            Human-readable description of the action the agent wants to
            perform (e.g. ``"delete old_config.py"``).
        language:
            ``"en"``, ``"ru"``, or ``"uk"`` — determines the spoken
            question language.

        Returns
        -------
        bool
            ``True`` if the user confirmed; ``False`` if the user
            declined, the response was ambiguous after a retry, or the
            voice callbacks are not wired (safe default: ``False``).
            When :attr:`enabled` is ``False``, returns ``True``
            (backward-compatible no-op).
        """
        # If callbacks are not wired — skip confirmation (backward compat).
        if not self.enabled:
            return True

        assert self._speak is not None  # for type-checker
        assert self._listen is not None

        # Build question in the correct language.
        question = self._build_question(action_description, language)

        # Speak the question.
        await self._speak(question)

        # Listen for response (first attempt).
        response = await self._listen(_FIRST_LISTEN_TIMEOUT)

        if response is None:
            # Timeout — safe default is NO.
            return False

        text = response.strip().lower()
        if _POSITIVE.match(text):
            return True
        if _NEGATIVE.match(text):
            return False

        # Ambiguous — ask again.
        retry_prompt = self._build_retry_prompt(language)
        await self._speak(retry_prompt)

        response2 = await self._listen(_RETRY_LISTEN_TIMEOUT)
        if response2 and _POSITIVE.match(response2.strip().lower()):
            return True
        return False  # default NO

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _build_question(action_description: str, language: str) -> str:
        """Build the confirmation question in the given language."""
        if language == "ru":
            return f"Sir, я хочу {action_description}. Подтвердите?"
        if language == "uk":
            return f"Sir, я хочу {action_description}. Підтвердіть?"
        return f"Sir, I'd like to {action_description}. May I proceed?"

    @staticmethod
    def _build_retry_prompt(language: str) -> str:
        """Build the retry prompt when the first answer was ambiguous."""
        if language == "ru":
            return "Не понял, sir. Скажите 'да' или 'нет'."
        if language == "uk":
            return "Не зрозумів, sir. Скажіть 'так' або 'ні'."
        return "I didn't catch that, sir. Say 'yes' or 'no'."
