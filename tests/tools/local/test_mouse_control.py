"""Tests for jarvis.tools.local.mouse_control. pyautogui + OCR are mocked —
no real cursor moves and no real screen capture."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from jarvis.tools.local.mouse_control import (
    MouseClickArgs,
    MouseClickTool,
    MouseDragArgs,
    MouseDragTool,
    MouseMoveArgs,
    MouseMoveTool,
    MousePositionArgs,
    MousePositionTool,
    MouseScrollArgs,
    MouseScrollTool,
)

_MOD = "jarvis.tools.local.mouse_control"


def _fake_pyautogui():
    pg = MagicMock()
    pg.position.return_value = (640, 480)
    return pg


def _match(tool_cls, phrase):
    for vp in sorted(tool_cls.voice_patterns, key=lambda p: p.priority):
        m = re.match(vp.regex, phrase.strip().lower())
        if m:
            return vp.args(m)
    return None


# ── click: coordinates ─────────────────────────────────────────────

class TestClick:
    async def test_click_coordinates(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseClickTool().execute(MouseClickArgs(x=500, y=300))
        assert r.success
        pg.click.assert_called_once()
        assert pg.click.call_args.args[:2] == (500, 300)

    async def test_double_click_coordinates(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseClickTool().execute(
                MouseClickArgs(x=10, y=20, button="double")
            )
        assert r.success
        pg.doubleClick.assert_called_once_with(10, 20)

    async def test_right_click_coordinates(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseClickTool().execute(
                MouseClickArgs(x=10, y=20, button="right")
            )
        assert r.success
        assert pg.click.call_args.kwargs.get("button") == "right"

    async def test_click_text_via_ocr(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch(f"{_MOD}._find_text_on_screen", return_value=(120, 240, 95.0)):
            r = await MouseClickTool().execute(MouseClickArgs(text="Save"))
        assert r.success
        assert pg.click.call_args.args[:2] == (120, 240)

    async def test_click_text_not_found(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch(f"{_MOD}._find_text_on_screen", return_value=None):
            r = await MouseClickTool().execute(MouseClickArgs(text="Ghost"))
        assert not r.success
        assert "couldn't find" in (r.error or "").lower()

    async def test_click_text_low_confidence_refused(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch(f"{_MOD}._find_text_on_screen", return_value=(1, 2, 50.0)):
            r = await MouseClickTool().execute(MouseClickArgs(text="Maybe"))
        assert not r.success
        pg.click.assert_not_called()

    async def test_click_no_target_errors(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseClickTool().execute(MouseClickArgs())
        assert not r.success

    async def test_no_pyautogui(self):
        with patch(f"{_MOD}._get_pyautogui", return_value=None):
            r = await MouseClickTool().execute(MouseClickArgs(x=1, y=2))
        assert not r.success
        assert "pyautogui" in (r.error or "").lower()


# ── move ────────────────────────────────────────────────────────────

class TestMove:
    async def test_move_coordinates(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseMoveTool().execute(MouseMoveArgs(x=100, y=200, duration=0.0))
        assert r.success
        pg.moveTo.assert_called_once()
        assert pg.moveTo.call_args.args[:2] == (100, 200)

    async def test_move_to_text(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch(f"{_MOD}._find_text_on_screen", return_value=(50, 60, 90.0)):
            r = await MouseMoveTool().execute(MouseMoveArgs(text="OK"))
        assert r.success
        assert pg.moveTo.call_args.args[:2] == (50, 60)


# ── scroll ───────────────────────────────────────────────────────────

class TestScroll:
    async def test_scroll_down(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseScrollTool().execute(MouseScrollArgs(direction="down", amount=3))
        assert r.success
        # down => negative delta
        assert pg.scroll.call_args.args[0] < 0

    async def test_scroll_up(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseScrollTool().execute(MouseScrollArgs(direction="up", amount=2))
        assert r.success
        assert pg.scroll.call_args.args[0] > 0

    async def test_scroll_bad_direction(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseScrollTool().execute(MouseScrollArgs(direction="sideways"))
        assert not r.success


# ── drag ──────────────────────────────────────────────────────────────

class TestDrag:
    async def test_drag(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MouseDragTool().execute(
                MouseDragArgs(from_x=100, from_y=200, to_x=500, to_y=400, duration=0.0)
            )
        assert r.success
        pg.moveTo.assert_called_once_with(100, 200)
        pg.dragTo.assert_called_once()
        assert pg.dragTo.call_args.args[:2] == (500, 400)


# ── position ──────────────────────────────────────────────────────────

class TestPosition:
    async def test_position(self):
        pg = _fake_pyautogui()
        with patch(f"{_MOD}._get_pyautogui", return_value=pg):
            r = await MousePositionTool().execute(MousePositionArgs())
        assert r.success
        assert "640" in r.output and "480" in r.output


# ── OCR helper ────────────────────────────────────────────────────────

class TestFindText:
    def test_returns_center_of_best_match(self):
        from jarvis.tools.local.mouse_control import _find_text_on_screen
        pg = _fake_pyautogui()
        regions = [
            {"text": "Save", "x": 100, "y": 200, "w": 40, "h": 20, "confidence": 92.0},
            {"text": "Save As", "x": 300, "y": 400, "w": 60, "h": 20, "confidence": 70.0},
        ]
        fake_ocr = MagicMock()
        fake_ocr.extract_regions.return_value = regions
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch("jarvis.vision.ocr.ScreenOCR", return_value=fake_ocr):
            res = _find_text_on_screen("save")
        # best confidence match → center of first region
        assert res == (100 + 20, 200 + 10, 92.0)

    def test_returns_none_when_absent(self):
        from jarvis.tools.local.mouse_control import _find_text_on_screen
        pg = _fake_pyautogui()
        fake_ocr = MagicMock()
        fake_ocr.extract_regions.return_value = []
        with patch(f"{_MOD}._get_pyautogui", return_value=pg), \
             patch("jarvis.vision.ocr.ScreenOCR", return_value=fake_ocr):
            assert _find_text_on_screen("whatever") is None


# ── voice-pattern routing ─────────────────────────────────────────────

class TestRouting:
    def test_click_patterns(self):
        assert _match(MouseClickTool, "click at 500 300") == {"x": 500, "y": 300, "button": "left"}
        assert _match(MouseClickTool, "click on save")["button"] == "left"
        assert _match(MouseClickTool, "double click on icon")["button"] == "double"
        assert _match(MouseClickTool, "right click on file")["button"] == "right"
        assert _match(MouseClickTool, "нажми на сохранить")["text"] == "сохранить"

    def test_scroll_patterns(self):
        assert _match(MouseScrollTool, "scroll down") == {"direction": "down", "amount": 3}
        assert _match(MouseScrollTool, "scroll up 10") == {"direction": "up", "amount": 10}
        assert _match(MouseScrollTool, "прокрути вниз")["direction"] == "down"

    def test_drag_patterns(self):
        a = _match(MouseDragTool, "drag from 100 200 to 500 400")
        assert a == {"from_x": 100, "from_y": 200, "to_x": 500, "to_y": 400}

    def test_move_patterns(self):
        a = _match(MouseMoveTool, "move mouse to 500 300")
        assert a == {"x": 500, "y": 300}

    def test_position_patterns(self):
        assert _match(MousePositionTool, "where is the mouse") == {}
        assert _match(MousePositionTool, "где мышь") == {}


def test_risk_levels():
    from jarvis.tools.risk import TOOL_RISK_MAP, RiskLevel
    assert TOOL_RISK_MAP["mouse_click"] == RiskLevel.WRITE
    assert TOOL_RISK_MAP["mouse_position"] == RiskLevel.READ
