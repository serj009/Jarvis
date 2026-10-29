"""Tests for jarvis.vision.ocr.ScreenOCR.

pytesseract and the Tesseract binary are mocked so the suite runs on
CI without system-level OCR installed.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from tests._typing import fake_module

# ── helpers ──────────────────────────────────────────────────────────

def _make_fake_pytesseract(
    *,
    text: str = "Hello World",
    data: dict | None = None,
    available: bool = True,
) -> types.ModuleType:
    """Build a fake pytesseract module with controllable outputs."""

    class _Output:
        DICT = "dict"

    mod = types.ModuleType("pytesseract")
    mod.__dict__["image_to_string"] = MagicMock(return_value=text)
    mod.__dict__["image_to_data"] = MagicMock(
        return_value=data or {
            "text": ["Hello", "World", ""],
            "conf": [95.0, 88.5, -1],
            "left": [10, 80, 0],
            "top": [20, 20, 0],
            "width": [60, 70, 0],
            "height": [15, 15, 0],
        },
    )
    mod.__dict__["Output"] = _Output
    if available:
        mod.__dict__["get_tesseract_version"] = MagicMock(return_value="5.3.0")
    else:
        mod.__dict__["get_tesseract_version"] = MagicMock(
            side_effect=RuntimeError("tesseract not found"),
        )
    return mod


def _make_fake_pil_image(size: tuple[int, int] = (1920, 1080)) -> MagicMock:
    """A minimal PIL.Image.Image stand-in that can .convert()."""
    img = MagicMock()
    img.size = size
    img.convert.return_value = img
    return img


def _make_enhance_module() -> types.ModuleType:
    """Fake PIL.ImageEnhance that returns the image unchanged."""
    enhancer_instance = MagicMock()
    enhancer_instance.enhance.return_value = MagicMock()

    class _Contrast:
        def __init__(self, image):  # noqa: ANN001
            self._image = image

        def enhance(self, factor):  # noqa: ANN001
            return enhancer_instance.enhance(factor)

    mod = types.ModuleType("PIL.ImageEnhance")
    mod.__dict__["Contrast"] = _Contrast
    return mod


# ── is_available ─────────────────────────────────────────────────────

def test_is_available_true_when_tesseract_installed():
    fake_pt = _make_fake_pytesseract(available=True)
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        from jarvis.vision.ocr import ScreenOCR
        assert ScreenOCR.is_available() is True


def test_is_available_false_when_tesseract_missing():
    fake_pt = _make_fake_pytesseract(available=False)
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        from jarvis.vision.ocr import ScreenOCR
        assert ScreenOCR.is_available() is False


def test_is_available_false_when_pytesseract_not_installed():
    """When pytesseract isn't even pip-installed, import fails."""
    with patch.dict(sys.modules, {"pytesseract": None}):
        from jarvis.vision.ocr import ScreenOCR
        assert ScreenOCR.is_available() is False


# ── extract_text ─────────────────────────────────────────────────────

def test_extract_text_returns_ocr_result():
    fake_pt = _make_fake_pytesseract(text="Error: file not found", available=True)
    fake_enhance = _make_enhance_module()
    image = _make_fake_pil_image()
    fake_pil = types.ModuleType("PIL")
    fake_pil.ImageEnhance = fake_enhance

    with patch.dict(sys.modules, {
        "pytesseract": fake_pt,
        "PIL": fake_pil,
        "PIL.ImageEnhance": fake_enhance,
    }):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR(languages="eng")
        result = ocr.extract_text(image)

    assert result == "Error: file not found"
    fake_pt.image_to_string.assert_called_once()


def test_extract_text_returns_empty_when_unavailable():
    fake_pt = _make_fake_pytesseract(available=False)
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR()
        result = ocr.extract_text(_make_fake_pil_image())
    assert result == ""
    fake_pt.image_to_string.assert_not_called()


def test_extract_text_returns_empty_on_exception():
    fake_pt = _make_fake_pytesseract(available=True)
    fake_pt.image_to_string.side_effect = RuntimeError("crash")
    fake_enhance = _make_enhance_module()
    image = _make_fake_pil_image()

    with patch.dict(sys.modules, {
        "pytesseract": fake_pt,
        "PIL.ImageEnhance": fake_enhance,
    }):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR()
        result = ocr.extract_text(image)
    assert result == ""


# ── extract_regions ──────────────────────────────────────────────────

def test_extract_regions_returns_bounding_boxes():
    data = {
        "text": ["HP:", "100", ""],
        "conf": [92.0, 87.0, -1],
        "left": [10, 50, 0],
        "top": [5, 5, 0],
        "width": [30, 40, 0],
        "height": [12, 12, 0],
    }
    fake_pt = _make_fake_pytesseract(data=data, available=True)
    fake_enhance = _make_enhance_module()
    image = _make_fake_pil_image()
    fake_pil = types.ModuleType("PIL")
    fake_pil.ImageEnhance = fake_enhance

    with patch.dict(sys.modules, {
        "pytesseract": fake_pt,
        "PIL": fake_pil,
        "PIL.ImageEnhance": fake_enhance,
    }):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR()
        regions = ocr.extract_regions(image)

    assert len(regions) == 2
    assert regions[0]["text"] == "HP:"
    assert regions[0]["x"] == 10
    assert regions[0]["confidence"] == 92.0
    assert regions[1]["text"] == "100"
    assert regions[1]["w"] == 40


def test_extract_regions_returns_empty_when_unavailable():
    fake_pt = _make_fake_pytesseract(available=False)
    with patch.dict(sys.modules, {"pytesseract": fake_pt}):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR()
        assert ocr.extract_regions(_make_fake_pil_image()) == []


def test_extract_regions_returns_empty_on_exception():
    fake_pt = _make_fake_pytesseract(available=True)
    fake_pt.image_to_data.side_effect = RuntimeError("boom")
    fake_enhance = _make_enhance_module()

    with patch.dict(sys.modules, {
        "pytesseract": fake_pt,
        "PIL.ImageEnhance": fake_enhance,
    }):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR()
        assert ocr.extract_regions(_make_fake_pil_image()) == []


# ── language config ──────────────────────────────────────────────────

def test_custom_languages_passed_to_tesseract():
    fake_pt = _make_fake_pytesseract(text="Привіт", available=True)
    fake_enhance = _make_enhance_module()
    image = _make_fake_pil_image()
    fake_pil = types.ModuleType("PIL")
    fake_pil.ImageEnhance = fake_enhance

    with patch.dict(sys.modules, {
        "pytesseract": fake_pt,
        "PIL": fake_pil,
        "PIL.ImageEnhance": fake_enhance,
    }):
        from jarvis.vision.ocr import ScreenOCR
        ocr = ScreenOCR(languages="ukr")
        ocr.extract_text(image)

    call_kwargs = fake_pt.image_to_string.call_args
    assert call_kwargs[1]["lang"] == "ukr" or call_kwargs.kwargs["lang"] == "ukr"
