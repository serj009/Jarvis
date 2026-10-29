"""Tests for jarvis.vision.screen_change.ScreenChangeDetector.

All screen-capture calls are mocked — no display server required.
PIL is used directly (not mocked) for the image operations because
the detector's correctness depends on real pixel arithmetic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# PIL is optional at runtime; skip the entire module when it is absent.
PIL = pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from jarvis.vision.screen_change import (  # noqa: E402
    _COMPARE_HEIGHT,
    _COMPARE_WIDTH,
    ScreenChangeDetector,
)

# --- helpers ----------------------------------------------------------

def _solid_image(
    colour: int = 128,
    size: tuple[int, int] = (1920, 1080),
) -> Image.Image:
    """Create a solid-colour RGB image."""
    return Image.new("RGB", size, (colour, colour, colour))


def _half_changed_image(
    base: int = 128,
    changed: int = 255,
    size: tuple[int, int] = (1920, 1080),
) -> Image.Image:
    """Image where the left half is `base` and the right half is `changed`."""
    img = Image.new("RGB", size, (base, base, base))
    w, h = size
    for y in range(h):
        for x in range(w // 2, w):
            img.putpixel((x, y), (changed, changed, changed))
    return img


def _patch_grab(image: Image.Image):
    """Patch ``PIL.ImageGrab.grab`` to return *image* -- no real capture.

    The detector does ``from PIL import ImageGrab`` lazily and then calls
    ``ImageGrab.grab(...)``. Patching the module attribute that call
    resolves to is what actually intercepts the capture; injecting a fake
    into ``sys.modules`` does not, because PIL.ImageGrab is already bound as
    an attribute on the PIL package by the time the test runs.
    """
    return patch("PIL.ImageGrab.grab", return_value=image)


# --- tests ------------------------------------------------------------

def test_identical_frames_no_change():
    """Two identical frames → diff_ratio ≈ 0, has_changed → False."""
    img = _solid_image(128)
    det = ScreenChangeDetector(threshold=0.02)

    with _patch_grab(img):
        # First call establishes baseline (always 1.0).
        assert det.diff_ratio() == 1.0
        # Second call — same image — should report no change.
        ratio = det.diff_ratio()

    assert ratio == 0.0
    # has_changed() would capture again; test the cached ratio instead.
    assert ratio <= det._threshold


def test_different_frames_detected():
    """A substantially different frame is detected as changed."""
    img_a = _solid_image(0)
    img_b = _solid_image(255)
    det = ScreenChangeDetector(threshold=0.02)

    with _patch_grab(img_a):
        det.diff_ratio()  # baseline

    with _patch_grab(img_b):
        ratio = det.diff_ratio()

    assert ratio > 0.5  # almost every pixel flipped


def test_threshold_sensitivity():
    """Lowering the threshold makes the detector more sensitive."""
    img_a = _solid_image(128)
    # Slightly different — only a few pixels change after quantisation.
    img_b = _solid_image(132)

    det_sensitive = ScreenChangeDetector(threshold=0.0)
    det_insensitive = ScreenChangeDetector(threshold=0.99)

    with _patch_grab(img_a):
        det_sensitive.diff_ratio()
        det_insensitive.diff_ratio()

    with _patch_grab(img_b):
        # Note: per-pixel threshold is 30, so 128→132 (diff=4) may NOT
        # exceed it. Both detectors should see ~0% change.
        ratio_s = det_sensitive.diff_ratio()
        ratio_i = det_insensitive.diff_ratio()

    # Both ratios are the same pixel-level computation.
    assert ratio_s == ratio_i
    # With per-pixel threshold of 30, a 4-value difference is below noise.
    assert ratio_s == 0.0

    # Now test with a real visible change.
    img_c = _solid_image(200)  # diff=72 from 128 — above per-pixel threshold
    det2 = ScreenChangeDetector(threshold=0.01)
    with _patch_grab(img_a):
        det2.diff_ratio()
    with _patch_grab(img_c):
        r = det2.diff_ratio()
    assert r > 0.5


def test_region_crop():
    """When a region is specified, ImageGrab.grab receives a bbox."""
    region = (100, 200, 400, 300)
    det = ScreenChangeDetector(threshold=0.02, region=region)

    img = _solid_image(128, size=(400, 300))

    with patch("PIL.ImageGrab.grab", return_value=img) as fake_grab:
        captured = det.capture()

    fake_grab.assert_called_once_with(bbox=(100, 200, 500, 500))
    assert captured is img


def test_diff_ratio_range():
    """diff_ratio is always in [0.0, 1.0]."""
    img_a = _solid_image(0)
    img_b = _solid_image(255)
    det = ScreenChangeDetector()

    with _patch_grab(img_a):
        r1 = det.diff_ratio()
    assert 0.0 <= r1 <= 1.0

    with _patch_grab(img_b):
        r2 = det.diff_ratio()
    assert 0.0 <= r2 <= 1.0


def test_diff_image_highlights():
    """get_diff_image returns an RGB image with red-tinted changed pixels."""
    img_a = _solid_image(0)
    img_b = _solid_image(255)
    det = ScreenChangeDetector()

    # No baseline → None.
    with _patch_grab(img_a):
        assert det.get_diff_image() is None

    # Establish baseline.
    with _patch_grab(img_a):
        det.diff_ratio()

    # Now get_diff_image with a different frame.
    with _patch_grab(img_b):
        diff_img = det.get_diff_image()

    assert diff_img is not None
    assert diff_img.mode == "RGB"
    assert diff_img.size == (_COMPARE_WIDTH, _COMPARE_HEIGHT)

    # At least some pixels should be reddish (R > G and R > B).
    pixels = list(diff_img.getdata())
    red_count = sum(1 for r, g, b in pixels if r > g and r > b)
    assert red_count > len(pixels) * 0.5


def test_reset_clears_baseline():
    """After reset(), the next diff_ratio() returns 1.0 (no baseline)."""
    img = _solid_image(128)
    det = ScreenChangeDetector()

    with _patch_grab(img):
        det.diff_ratio()  # establish baseline
        det.reset()
        ratio = det.diff_ratio()  # should be 1.0 again

    assert ratio == 1.0


def test_grayscale_resize_performance():
    """The internal comparison frame is exactly _COMPARE_WIDTH × _COMPARE_HEIGHT."""
    img = _solid_image(100, size=(3840, 2160))  # 4K
    det = ScreenChangeDetector()

    with _patch_grab(img):
        det.diff_ratio()

    assert det._last_frame is not None
    assert det._last_frame.size == (_COMPARE_WIDTH, _COMPARE_HEIGHT)
    assert det._last_frame.mode == "L"


def test_invalid_threshold_raises():
    """Thresholds outside [0, 1] raise ValueError."""
    with pytest.raises(ValueError, match="threshold"):
        ScreenChangeDetector(threshold=-0.1)
    with pytest.raises(ValueError, match="threshold"):
        ScreenChangeDetector(threshold=1.5)
