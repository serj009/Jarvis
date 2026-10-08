"""Tests for jarvis.core.errors (T1.5 Error Handler)."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.errors import (
    ErrorHandler,
    JarvisError,
    STTError,
    TTSError,
    VRAMError,
)
from jarvis.core.phrases import PHRASES


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_all_inherit_from_jarvis_error(self) -> None:
        assert issubclass(STTError, JarvisError)
        assert issubclass(TTSError, JarvisError)
        assert issubclass(VRAMError, JarvisError)

    def test_spoken_keys_exist_in_phrases(self) -> None:
        for err_cls in (JarvisError, STTError, TTSError, VRAMError):
            key = err_cls.spoken_key
            assert key in PHRASES, f"{err_cls.__name__}.spoken_key={key!r} not in PHRASES"
            for lang in ("en", "ru", "uk"):
                assert lang in PHRASES[key], f"PHRASES[{key!r}] missing {lang}"

    def test_custom_spoken_key(self) -> None:
        err = JarvisError("boom", spoken_key="error_vram")
        assert err.spoken_key == "error_vram"

    def test_default_message(self) -> None:
        err = STTError("mic broke")
        assert str(err) == "mic broke"
        assert err.spoken_key == "error_stt"


# ---------------------------------------------------------------------------
# ErrorHandler
# ---------------------------------------------------------------------------


class TestErrorHandler:
    @pytest.fixture()
    def handler(self) -> ErrorHandler:
        return ErrorHandler(bus=None)

    @pytest.mark.asyncio()
    async def test_successful_call_resets_counter(self, handler: ErrorHandler) -> None:
        async def ok() -> str:
            return "hello"

        result = await handler.safe_call(ok(), stage="test")
        assert result == "hello"
        assert handler.consecutive_errors == 0

    @pytest.mark.asyncio()
    async def test_failed_call_returns_sentinel(self, handler: ErrorHandler) -> None:
        async def boom() -> str:
            raise STTError("no mic")

        result = await handler.safe_call(boom(), stage="stt")
        assert result is handler.FAILED
        assert handler.consecutive_errors == 1

    @pytest.mark.asyncio()
    async def test_consecutive_errors_accumulate(self, handler: ErrorHandler) -> None:
        for i in range(3):
            async def fail() -> None:
                raise TTSError("synth failed")

            await handler.safe_call(fail(), stage="tts")
        assert handler.consecutive_errors == 3

    @pytest.mark.asyncio()
    async def test_success_resets_after_errors(self, handler: ErrorHandler) -> None:
        async def fail() -> None:
            raise VRAMError("oom")

        async def ok() -> str:
            return "fine"

        await handler.safe_call(fail(), stage="vram")
        await handler.safe_call(fail(), stage="vram")
        assert handler.consecutive_errors == 2

        await handler.safe_call(ok(), stage="vram")
        assert handler.consecutive_errors == 0

    @pytest.mark.asyncio()
    async def test_generic_exception_still_caught(self, handler: ErrorHandler) -> None:
        async def fail() -> None:
            raise RuntimeError("unexpected")

        result = await handler.safe_call(fail(), stage="unknown")
        assert result is handler.FAILED
        assert handler.consecutive_errors == 1

    def test_spoken_message_returns_string(self, handler: ErrorHandler) -> None:
        msg = handler.spoken_message("error_stt")
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_spoken_message_fallback(self, handler: ErrorHandler) -> None:
        msg = handler.spoken_message("nonexistent_key")
        assert "went wrong" in msg.lower() or "не так" in msg.lower() or "пішло" in msg.lower()
