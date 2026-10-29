"""Tests for jarvis.tools.local.watch_screen.WatchScreenTool.

All screen-capture calls are mocked — no display server required.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.watch_screen import WatchScreenArgs, WatchScreenTool
from jarvis.tools.registry import VoicePattern

# --- voice-pattern tests ---------------------------------------------

_EN_PATTERNS = [
    ("watch the screen", "start"),
    ("watch screen", "start"),
    ("tell me when the screen changes", "wait"),
    ("notify me when screen changes", "wait"),
    ("stop watching the screen", "stop"),
    ("stop watching screen", "stop"),
]

_RU_PATTERNS = [
    ("следи за экраном", "start"),
    ("скажи когда экран изменится", "wait"),
    ("скажи когда экран поменяется", "wait"),
    ("перестань следить за экраном", "stop"),
    ("хватит следить за экраном", "stop"),
]

_UK_PATTERNS = [
    ("стеж за екраном", "start"),
    ("скажи коли екран зміниться", "wait"),
    ("скажи коли екран поміняється", "wait"),
    ("перестань стежити за екраном", "stop"),
    ("досить стежити за екраном", "stop"),
]


@pytest.mark.parametrize(
    "utterance, expected_action",
    _EN_PATTERNS + _RU_PATTERNS + _UK_PATTERNS,
    ids=[u for u, _ in _EN_PATTERNS + _RU_PATTERNS + _UK_PATTERNS],
)
def test_voice_patterns(utterance: str, expected_action: str):
    """Every listed utterance matches exactly one voice pattern."""
    patterns: tuple[VoicePattern, ...] = WatchScreenTool.voice_patterns
    matched = False
    for vp in patterns:
        m = re.match(vp.regex, utterance)
        if m:
            args = vp.args(m)
            assert args["action"] == expected_action, (
                f"Pattern {vp.regex!r} matched {utterance!r} but "
                f"produced action={args['action']!r}, expected {expected_action!r}"
            )
            matched = True
            break
    assert matched, f"No pattern matched {utterance!r}"


def test_voice_patterns_minimum_count():
    """At least 3 patterns per language (EN/RU/UK)."""
    patterns = WatchScreenTool.voice_patterns
    assert len(patterns) >= 9  # 3 × 3 languages


# --- lifecycle tests -------------------------------------------------

def _make_fake_detector(*, diff_ratios: list[float] | None = None):
    """Return a mock ScreenChangeDetector."""
    det = MagicMock()
    if diff_ratios is None:
        diff_ratios = [1.0, 0.0]

    call_count = 0

    def _diff_ratio():
        nonlocal call_count
        ratio = diff_ratios[min(call_count, len(diff_ratios) - 1)]
        call_count += 1
        det._last_diff_ratio = ratio
        return ratio

    def _has_changed():
        r = _diff_ratio()
        return r > det._threshold_val

    det.diff_ratio = _diff_ratio
    det.has_changed = _has_changed
    det._threshold_val = 0.02
    det._last_diff_ratio = 0.0
    return det


async def test_start_stop_lifecycle():
    """start → monitoring active; stop → monitoring inactive."""
    tool = WatchScreenTool()
    fake_det = _make_fake_detector()

    with patch.object(
        WatchScreenTool, "_make_detector", return_value=fake_det
    ):
        # Start
        result = await tool.execute(WatchScreenArgs(action="start"))
        assert result.success
        assert tool._monitoring is True

        # Start again — idempotent
        result = await tool.execute(WatchScreenArgs(action="start"))
        assert result.success
        assert "already" in (result.output or "").lower()

        # Stop
        result = await tool.execute(WatchScreenArgs(action="stop"))
        assert result.success
        assert tool._monitoring is False

        # Stop again — idempotent
        result = await tool.execute(WatchScreenArgs(action="stop"))
        assert result.success
        assert "wasn't" in (result.output or "").lower()


async def test_status_reports_change():
    """status action reports changed vs. unchanged."""
    tool = WatchScreenTool()
    # Build a detector where first diff_ratio → 1.0 (baseline),
    # second has_changed → True (0.5 > threshold).
    fake_det = _make_fake_detector(diff_ratios=[1.0, 0.5])

    with patch.object(
        WatchScreenTool, "_make_detector", return_value=fake_det
    ):
        # Start first.
        await tool.execute(WatchScreenArgs(action="start"))

        # Status — detector will report change.
        result = await tool.execute(WatchScreenArgs(action="status"))
        assert result.success
        assert "changed" in (result.output or "").lower()


async def test_status_without_start():
    """status without start returns an error."""
    tool = WatchScreenTool()
    result = await tool.execute(WatchScreenArgs(action="status"))
    assert not result.success
    assert "not active" in (result.error or "").lower()


async def test_wait_timeout():
    """wait action times out when no change is detected."""
    tool = WatchScreenTool()
    # All diff_ratios below threshold → no change ever.
    fake_det = _make_fake_detector(diff_ratios=[1.0, 0.0, 0.0, 0.0, 0.0])

    # has_changed always returns False (0.0 ≤ threshold 0.02).
    fake_det.has_changed = lambda: False
    fake_det._last_diff_ratio = 0.0

    with patch.object(
        WatchScreenTool, "_make_detector", return_value=fake_det
    ):
        result = await tool.execute(
            WatchScreenArgs(action="wait", timeout=2.0, interval=0.5)
        )

    assert result.success
    assert "no significant" in (result.output or "").lower()


async def test_wait_detects_change():
    """wait action returns as soon as a change is detected."""
    tool = WatchScreenTool()
    fake_det = _make_fake_detector(diff_ratios=[1.0])

    # has_changed: False on first poll, True on second.
    call_idx = 0

    def _has_changed():
        nonlocal call_idx
        call_idx += 1
        if call_idx >= 2:
            fake_det._last_diff_ratio = 0.15
            return True
        fake_det._last_diff_ratio = 0.0
        return False

    fake_det.has_changed = _has_changed

    with patch.object(
        WatchScreenTool, "_make_detector", return_value=fake_det
    ):
        result = await tool.execute(
            WatchScreenArgs(action="wait", timeout=10.0, interval=0.5)
        )

    assert result.success
    assert "changed" in (result.output or "").lower()


async def test_unknown_action():
    """An unrecognised action returns an error."""
    tool = WatchScreenTool()
    result = await tool.execute(WatchScreenArgs(action="foobar"))
    assert not result.success
    assert "unknown" in (result.error or "").lower()


def test_requires_confirmation_false():
    tool = WatchScreenTool()
    assert tool.requires_confirmation is False
