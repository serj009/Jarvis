"""Tesseract-based OCR for extracting text from screen captures.

Provides a lightweight, GPU-free alternative to vision models for
reading text visible on screen.  Falls back gracefully when Tesseract
is not installed — ``is_available()`` returns False and every extraction
method returns an empty result instead of raising.

Tesseract is a system-level binary (not a pip package).  Install via:
  - Windows: https://github.com/UB-Mannheim/tesseract/wiki
  - Linux:   sudo apt install tesseract-ocr
  - macOS:   brew install tesseract

The ``pytesseract`` pip package is the Python wrapper.  Install via:
  pip install pytesseract
or:
  pip install -e ".[ocr]"

Multi-language support: default ``eng+rus+ukr`` matches JARVIS's
tri-lingual voice patterns.  Additional Tesseract language packs can
be installed independently.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as _PILImage

log = logging.getLogger(__name__)


class ScreenOCR:
    """Extract text from screen captures using Tesseract OCR.

    Falls back gracefully if Tesseract is not installed.
    Supports multiple languages (eng+rus+ukr).
    """

    def __init__(self, languages: str = "eng+rus+ukr") -> None:
        """Initialise with Tesseract language string.

        Parameters
        ----------
        languages:
            Languages in Tesseract format, e.g. ``"eng+rus+ukr"``.
        """
        self._languages = languages

    # -- public API --------------------------------------------------------

    def extract_text(self, image: _PILImage.Image) -> str:
        """Extract all readable text from *image*.

        Returns an empty string when Tesseract is unavailable or when no
        text is found.
        """
        if not self.is_available():
            return ""
        try:
            import pytesseract  # lazy  # noqa: E402

            preprocessed = self._preprocess(image)
            text: str = pytesseract.image_to_string(
                preprocessed, lang=self._languages,
            )
            return text.strip()
        except Exception:
            log.debug("OCR extract_text failed", exc_info=True)
            return ""

    def extract_regions(self, image: _PILImage.Image) -> list[dict]:
        """Extract text with bounding boxes for UI element identification.

        Each entry: ``{"text": str, "x": int, "y": int, "w": int,
        "h": int, "confidence": float}``.

        Returns an empty list when Tesseract is unavailable.
        """
        if not self.is_available():
            return []
        try:
            import pytesseract  # lazy  # noqa: E402

            preprocessed = self._preprocess(image)
            data = pytesseract.image_to_data(
                preprocessed,
                lang=self._languages,
                output_type=pytesseract.Output.DICT,
            )

            regions: list[dict] = []
            n_boxes = len(data.get("text", []))
            for i in range(n_boxes):
                text = (data["text"][i] or "").strip()
                if not text:
                    continue
                conf_raw = data["conf"][i]
                try:
                    conf = float(conf_raw)
                except (TypeError, ValueError):
                    conf = 0.0
                if conf < 0:
                    continue
                regions.append({
                    "text": text,
                    "x": int(data["left"][i]),
                    "y": int(data["top"][i]),
                    "w": int(data["width"][i]),
                    "h": int(data["height"][i]),
                    "confidence": conf,
                })
            return regions
        except Exception:
            log.debug("OCR extract_regions failed", exc_info=True)
            return []

    @staticmethod
    def is_available() -> bool:
        """Check if Tesseract is installed and the pytesseract wrapper
        is importable."""
        try:
            import pytesseract  # lazy  # noqa: E402

            # pytesseract.get_tesseract_version() calls the binary —
            # confirms both the wrapper and the engine are present.
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _preprocess(image: _PILImage.Image) -> _PILImage.Image:
        """Pre-process for better OCR accuracy.

        Convert to grayscale and increase contrast to help Tesseract with
        anti-aliased screen text on coloured backgrounds.
        """
        from PIL import ImageEnhance  # lazy

        gray = image.convert("L")
        enhancer = ImageEnhance.Contrast(gray)
        return enhancer.enhance(2.0)
