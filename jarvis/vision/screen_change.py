"""Lightweight screen-change detector — pixel-level frame differencing.

Compares consecutive screen captures to determine whether the display has
changed significantly.  No heavy ML; uses PIL-based grayscale differencing
with a configurable threshold so the decision is fast enough to run in a
tight polling loop without saturating the GPU or CPU.

Typical use-cases:
1. Guard expensive vision-model calls — skip analysis when the screen is
   static.
2. "Tell me when the screen changes" — background monitoring.
3. Auto-trigger for ``SeeScreenTool`` — only describe when something new
   appears.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as _ImageModule

log = logging.getLogger(__name__)

# Internal constants — tuned for a 640×360 downscaled grayscale frame.
_COMPARE_WIDTH = 640
_COMPARE_HEIGHT = 360
_PER_PIXEL_THRESHOLD = 30  # out of 255; ignores sub-30 noise


class ScreenChangeDetector:
    """Detects significant visual changes between screen frames.

    Uses PIL-based frame differencing with configurable threshold.
    No heavy ML — just pixel-level comparison for speed.
    """

    def __init__(
        self,
        threshold: float = 0.02,
        region: tuple[int, int, int, int] | None = None,
    ) -> None:
        """
        Args:
            threshold: fraction of pixels that must change (0.0–1.0,
                       default 0.02 = 2%).
            region:    optional ``(x, y, w, h)`` to monitor only part of
                       the screen.  Coordinates are in *physical* (screen)
                       pixels.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"threshold must be between 0.0 and 1.0, got {threshold}"
            )
        self._threshold = threshold
        self._region = region
        self._last_frame: _ImageModule.Image | None = None
        self._last_diff_ratio: float = 0.0

    # -- public API ---------------------------------------------------

    def capture(self) -> _ImageModule.Image:
        """Capture current screen (or region).

        Returns the *raw* (full-colour, full-resolution) capture — the
        downscale to comparison size happens internally in
        ``has_changed`` / ``diff_ratio``.
        """
        from PIL import ImageGrab

        if self._region is not None:
            x, y, w, h = self._region
            bbox = (x, y, x + w, y + h)
            return ImageGrab.grab(bbox=bbox)
        return ImageGrab.grab()

    def has_changed(self) -> bool:
        """Compare current frame to last stored frame.

        On the first call (no stored frame) the method returns ``True``
        so that the caller always processes the initial state.

        Side-effect: stores the current frame as the new baseline.
        """
        return self.diff_ratio() > self._threshold

    def diff_ratio(self) -> float:
        """Return the fraction of pixels that changed (0.0–1.0).

        Captures a fresh frame, compares it to the stored baseline, then
        replaces the baseline with the fresh frame.

        Returns 1.0 on the first call (no baseline yet).
        """
        current = self.capture()
        current_small = self._to_comparable(current)

        if self._last_frame is None:
            self._last_frame = current_small
            self._last_diff_ratio = 1.0
            return 1.0

        ratio = self._compute_diff_ratio(self._last_frame, current_small)
        self._last_frame = current_small
        self._last_diff_ratio = ratio
        return ratio

    def get_diff_image(self) -> _ImageModule.Image | None:
        """Return a visual diff highlighting changed regions (red overlay).

        Captures a fresh frame, compares it to the stored baseline, and
        returns an RGB image where changed pixels are tinted red.

        Returns ``None`` when no baseline exists (first call — use
        ``has_changed`` or ``diff_ratio`` first to establish a baseline).
        """
        if self._last_frame is None:
            return None

        from PIL import Image

        current = self.capture()
        current_small = self._to_comparable(current)

        diff_mask = self._compute_diff_mask(self._last_frame, current_small)

        # Build an RGB diff image: unchanged pixels are grey, changed
        # pixels are bright red.
        rgb = current_small.convert("RGB")
        pixels = rgb.load()
        w, h = rgb.size
        for py in range(h):
            for px in range(w):
                if diff_mask[py * w + px]:
                    # Tint changed pixel red.
                    r, g, b = pixels[px, py]
                    pixels[px, py] = (min(255, r + 128), g // 2, b // 2)

        return rgb

    def reset(self) -> None:
        """Clear stored frame; next ``has_changed()`` will return True."""
        self._last_frame = None
        self._last_diff_ratio = 0.0

    # -- internals ----------------------------------------------------

    @staticmethod
    def _to_comparable(image: _ImageModule.Image) -> _ImageModule.Image:
        """Convert a capture to a small grayscale frame for fast comparison."""
        from PIL import Image

        return image.convert("L").resize(
            (_COMPARE_WIDTH, _COMPARE_HEIGHT),
            Image.Resampling.LANCZOS,
        )

    @staticmethod
    def _compute_diff_ratio(
        a: _ImageModule.Image,
        b: _ImageModule.Image,
    ) -> float:
        """Fraction of pixels whose absolute brightness difference exceeds
        ``_PER_PIXEL_THRESHOLD``."""
        a_bytes = a.tobytes()
        b_bytes = b.tobytes()
        total = len(a_bytes)
        if total == 0:
            return 0.0
        changed = sum(
            1
            for av, bv in zip(a_bytes, b_bytes)
            if abs(av - bv) > _PER_PIXEL_THRESHOLD
        )
        return changed / total

    @staticmethod
    def _compute_diff_mask(
        a: _ImageModule.Image,
        b: _ImageModule.Image,
    ) -> list[bool]:
        """Per-pixel boolean mask: True where the pixel changed."""
        a_bytes = a.tobytes()
        b_bytes = b.tobytes()
        return [
            abs(av - bv) > _PER_PIXEL_THRESHOLD
            for av, bv in zip(a_bytes, b_bytes)
        ]
