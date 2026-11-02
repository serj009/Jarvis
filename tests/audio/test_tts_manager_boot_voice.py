"""TTSManager must switch to the right voice even when the boot language
already equals the detected language.

Live regression 2026-11-02: last_language.json said "ru", so TTSManager
started with current_language="ru", but Piper was created with the English
voice. set_language("ru") saw "ru == ru", never switched, and the English
voice read Russian replies as gibberish.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.audio.tts_manager import TTSManager
from jarvis.audio.voice_registry import VoiceRegistry


def _registry(
    tmp_path: Path, *, downloaded: tuple[str, ...] = ("en", "ru", "uk"),
) -> VoiceRegistry:
    reg = VoiceRegistry(voices_dir=tmp_path)
    for lang in downloaded:
        name = reg.get_voice_name(lang)
        (tmp_path / f"{name}.onnx").touch()
        (tmp_path / f"{name}.onnx.json").touch()
    return reg


def _manager(
    reg: VoiceRegistry, *, loaded_lang: str, boot_lang: str,
) -> tuple[TTSManager, MagicMock]:
    piper = MagicMock()
    piper.voice_name = reg.get_voice_name(loaded_lang)
    piper.activate_voice = AsyncMock()
    piper.speak_stream = AsyncMock()
    return TTSManager(piper_tts=piper, voice_registry=reg, default_language=boot_lang), piper


@pytest.mark.asyncio
async def test_boot_language_ru_with_english_voice_switches(tmp_path):
    reg = _registry(tmp_path)
    mgr, piper = _manager(reg, loaded_lang="en", boot_lang="ru")

    await mgr.set_language("ru")

    piper.activate_voice.assert_awaited_once_with(reg.get_voice_name("ru"))
    assert mgr.current_voice == reg.get_voice_name("ru")
    assert mgr.current_language == "ru"


@pytest.mark.asyncio
async def test_speak_stream_also_fixes_the_mismatch(tmp_path):
    reg = _registry(tmp_path)
    mgr, piper = _manager(reg, loaded_lang="en", boot_lang="ru")

    async def chunks():
        yield "Привет"

    await mgr.speak_stream(chunks(), language="ru")

    piper.activate_voice.assert_awaited_once_with(reg.get_voice_name("ru"))


@pytest.mark.asyncio
async def test_matching_voice_is_not_reloaded(tmp_path):
    reg = _registry(tmp_path)
    mgr, piper = _manager(reg, loaded_lang="ru", boot_lang="ru")

    await mgr.set_language("ru")
    await mgr.set_language("ru")

    piper.activate_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_voice_warns_once_and_keeps_current(tmp_path, caplog):
    reg = _registry(tmp_path, downloaded=("en",))
    mgr, piper = _manager(reg, loaded_lang="en", boot_lang="en")

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await mgr.set_language("ru")

    piper.activate_voice.assert_not_awaited()
    assert mgr.current_voice == reg.get_voice_name("en")
    warnings = [r for r in caplog.records if "not downloaded" in r.getMessage()]
    assert len(warnings) == 1
