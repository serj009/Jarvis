"""Tests for jarvis.tools.local.read_screen_text.ReadScreenTextTool.

pyautogui and pytesseract are mocked — runs headlessly and offline.
"""

from __future__ import annotations

import re
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.tools.local.read_screen_text import (
    ReadScreenTextArgs,
    ReadScreenTextTool,
)
from jarvis.vision.ocr import ScreenOCR
from tests._typing import fake_module


# ── helpers ──────────────────────────────────────────────────────────

def _make_ocr_mock(*, available: bool = True, text: str = "Login") -> MagicMock:
    """Build a mock ScreenOCR with controllable outputs."""
    ocr = MagicMock(spec=ScreenOCR)
    ocr.is_available.return_value = available
    ocr.extract_text.return_value = text
    return ocr


class _FakeImage:
    """Minimal PIL.Image.Image stub."""
    def __init__(self, size: tuple[int, int] = (1920, 1080)) -> None:
        self.size = size


def _install_fake_pyautogui(image: _FakeImage | None = None) -> dict[str, types.ModuleType]:
    img = image or _FakeImage()
    return {
        "pyautogui": fake_module("pyautogui", screenshot=MagicMock(return_value=img)),
    }


# ── execute ──────────────────────────────────────────────────────────

async def test_returns_ocr_text(tmp_path):
    ocr = _make_ocr_mock(available=True, text="Error: connection refused")
    tool = ReadScreenTextTool(ocr=ocr)
    fakes = _install_fake_pyautogui()

    with patch.dict(sys.modules, fakes):
        result = await tool.execute(ReadScreenTextArgs())

    assert result.success
    assert result.output == "Error: connection refused"
    ocr.extract_text.assert_called_once()


async def test_returns_no_text_message_when_empty():
    ocr = _make_ocr_mock(available=True, text="   ")
    tool = ReadScreenTextTool(ocr=ocr)
    fakes = _install_fake_pyautogui()

    with patch.dict(sys.modules, fakes):
        result = await tool.execute(ReadScreenTextArgs())

    assert result.success
    assert "don't see any readable text" in (result.output or "").lower()


async def test_returns_error_when_tesseract_unavailable():
    ocr = _make_ocr_mock(available=False)
    tool = ReadScreenTextTool(ocr=ocr)

    result = await tool.execute(ReadScreenTextArgs())

    assert not result.success
    assert "tesseract" in (result.error or "").lower()
    ocr.extract_text.assert_not_called()


async def test_returns_error_on_screenshot_failure():
    ocr = _make_ocr_mock(available=True)
    tool = ReadScreenTextTool(ocr=ocr)

    fake_pyautogui = fake_module(
        "pyautogui",
        screenshot=MagicMock(side_effect=RuntimeError("no display")),
    )
    with patch.dict(sys.modules, {"pyautogui": fake_pyautogui}):
        result = await tool.execute(ReadScreenTextArgs())

    assert not result.success
    assert "couldn't capture" in (result.error or "").lower()


def test_requires_confirmation_false():
    tool = ReadScreenTextTool()
    assert tool.requires_confirmation is False


# ── voice patterns ───────────────────────────────────────────────────

_VOICE_CASES_EN = [
    "read the text on the screen",
    "read text on screen",
    "read the words on the screen",
    "what does it say",
    "what does that say",
    "what do the words say",
    "extract the text from the screen",
    "grab text from screen",
    "get the text on the monitor",
]

_VOICE_CASES_RU = [
    "прочитай текст на экране",
    "прочти текст на экране",
    "прочитай экран",
    "что там написано",
    "что написано на экране",
    "что за текст на экране",
    "покажи мне текст на экране",
    "скажи текст на экране",
    "назови текст с экрана",
]

_VOICE_CASES_UK = [
    "прочитай текст на екрані",
    "прочти текст на екрані",
    "прочитай екран",
    "що там написано",
    "що написано на екрані",
    "що за текст на екрані",
    "покажи мені текст на екрані",
    "скажи текст з екрану",
    "назви текст на екрані",
]


@pytest.mark.parametrize(
    "utterance",
    _VOICE_CASES_EN + _VOICE_CASES_RU + _VOICE_CASES_UK,
)
def test_voice_pattern_matches(utterance: str):
    """Every documented voice command must match at least one pattern."""
    patterns = ReadScreenTextTool.voice_patterns
    assert any(
        re.match(p.regex, utterance, re.IGNORECASE) for p in patterns
    ), f"No pattern matched: {utterance!r}"


def test_voice_pattern_does_not_match_unrelated():
    """Sanity: unrelated phrases must NOT match any pattern."""
    garbage = [
        "open spotify",
        "set a timer for 5 minutes",
        "what's the weather",
        "describe my screen",
    ]
    patterns = ReadScreenTextTool.voice_patterns
    for phrase in garbage:
        assert not any(
            re.match(p.regex, phrase, re.IGNORECASE) for p in patterns
        ), f"Pattern should NOT have matched: {phrase!r}"
