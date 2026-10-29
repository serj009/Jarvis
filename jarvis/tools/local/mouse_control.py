"""Mouse control — click, move, scroll, drag, and report position.

Complements the existing UI tools (type_into_active_window, hotkey_sender,
window_manager) with pointer control. Two targeting modes:

- **coordinates**: click/move to an absolute (x, y).
- **on-screen text**: find a word via OCR (reusing jarvis.vision.ocr.
  ScreenOCR.extract_regions) and click its centre.

Safety:
- ``pyautogui.FAILSAFE = True`` — slamming the cursor into a screen corner
  aborts any pyautogui call.
- ``pyautogui.PAUSE = 0.1`` — a small gap between actions.
- OCR clicks on a low-confidence match (<80%) are refused with a message
  so Jarvis never blindly clicks the wrong thing.

pyautogui is already a project dependency (hotkey/screenshot); no new deps.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel

log = logging.getLogger(__name__)

# Confidence (0–100) below which an OCR-targeted click is refused.
_OCR_MIN_CONFIDENCE = 80.0


def _get_pyautogui():
    """Lazy import pyautogui with the project's safety defaults applied."""
    try:
        import pyautogui
    except ImportError:
        return None
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    return pyautogui


def _find_text_on_screen(text: str) -> tuple[int, int, float] | None:
    """Locate *text* on screen via OCR. Returns (cx, cy, confidence) or None.

    Reuses ScreenOCR.extract_regions — screenshot, OCR to word boxes, then
    the best case-insensitive substring match by confidence. The returned
    point is the centre of the matched word's bounding box.
    """
    pyautogui = _get_pyautogui()
    if pyautogui is None:
        return None
    try:
        shot = pyautogui.screenshot()
    except Exception:
        log.debug("screenshot for OCR click failed", exc_info=True)
        return None

    from jarvis.vision.ocr import ScreenOCR

    regions = ScreenOCR().extract_regions(shot)
    needle = text.strip().lower()
    best: dict | None = None
    for r in regions:
        if needle in r["text"].lower():
            if best is None or r["confidence"] > best["confidence"]:
                best = r
    if best is None:
        return None
    cx = best["x"] + best["w"] // 2
    cy = best["y"] + best["h"] // 2
    return (cx, cy, best["confidence"])


# ---------------------------------------------------------------------------
# 1. Click
# ---------------------------------------------------------------------------

class MouseClickArgs(BaseModel):
    x: int | None = Field(default=None, description="X coordinate (with y).")
    y: int | None = Field(default=None, description="Y coordinate (with x).")
    text: str | None = Field(
        default=None,
        description="On-screen text to find via OCR and click. Overrides x/y.",
    )
    button: str = Field(
        default="left", description="Button: left, right, middle, or double."
    )
    clicks: int = Field(default=1, ge=1, le=3, description="Number of clicks.")


class MouseClickTool:
    """Click the mouse at coordinates or on on-screen text (via OCR)."""

    name: str = "mouse_click"
    description: str = (
        "Click the mouse — at (x, y) coordinates, or on a piece of text "
        "visible on screen (found via OCR). Supports left/right/middle and "
        "double-click. (RU: нажми/кликни на … / UK: натисни/клікни на …)"
    )
    args_schema: type[BaseModel] = MouseClickArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: coordinates
        VoicePattern(
            regex=r"^click\s+(?:at\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2)), "button": "left"},
        ),
        VoicePattern(
            regex=r"^double[\s-]?click\s+(?:at\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=229,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2)), "button": "double"},
        ),
        VoicePattern(
            regex=r"^right[\s-]?click\s+(?:at\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=229,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2)), "button": "right"},
        ),
        # EN: on text
        VoicePattern(
            regex=r"^double[\s-]?click\s+(?:on\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "double"},
        ),
        VoicePattern(
            regex=r"^right[\s-]?click\s+(?:on\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "right"},
        ),
        VoicePattern(
            regex=r"^click\s+(?:on\s+)?(.+)$",
            priority=240,
            args=lambda m: {"text": m.group(1), "button": "left"},
        ),
        # RU: coordinates
        VoicePattern(
            regex=r"^(?:кликни|клик|нажми)\s+(?:на\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2)), "button": "left"},
        ),
        VoicePattern(
            regex=r"^двойной\s+клик\s+(?:на\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "double"},
        ),
        VoicePattern(
            regex=r"^правый\s+клик\s+(?:на\s+|по\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "right"},
        ),
        VoicePattern(
            regex=r"^(?:нажми|кликни)\s+(?:на\s+|по\s+)(.+)$",
            priority=240,
            args=lambda m: {"text": m.group(1), "button": "left"},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:клікни|клік|натисни)\s+(?:на\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2)), "button": "left"},
        ),
        VoicePattern(
            regex=r"^подвійний\s+клік\s+(?:на\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "double"},
        ),
        VoicePattern(
            regex=r"^правий\s+клік\s+(?:на\s+|по\s+)?(.+)$",
            priority=235,
            args=lambda m: {"text": m.group(1), "button": "right"},
        ),
        VoicePattern(
            regex=r"^(?:натисни|клікни)\s+(?:на\s+|по\s+)(.+)$",
            priority=240,
            args=lambda m: {"text": m.group(1), "button": "left"},
        ),
    )

    async def execute(self, args: MouseClickArgs) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult(
                success=False,
                error="Mouse control needs pyautogui. Install: pip install pyautogui",
            )

        target_x, target_y = args.x, args.y
        where = ""
        if args.text:
            found = await asyncio.to_thread(_find_text_on_screen, args.text)
            if found is None:
                return ToolResult(
                    success=False,
                    error=f"I couldn't find '{args.text}' on screen, sir.",
                )
            target_x, target_y, conf = found
            if conf < _OCR_MIN_CONFIDENCE:
                return ToolResult(
                    success=False,
                    error=(
                        f"I'm only {conf:.0f}% sure where '{args.text}' is, sir — "
                        "too low to click safely. Try the exact coordinates."
                    ),
                )
            where = f" on '{args.text}'"
        if target_x is None or target_y is None:
            return ToolResult(
                success=False,
                error="Give me coordinates or on-screen text to click, sir.",
            )

        button = args.button.lower()
        try:
            await asyncio.to_thread(
                self._do_click, pyautogui, target_x, target_y, button, args.clicks
            )
        except Exception as e:
            log.exception("mouse_click failed")
            return ToolResult(success=False, error=f"Click failed: {e}")

        log.info("[mouse] click button=%s at (%s,%s)%s", button, target_x, target_y, where)
        verb = {
            "left": "Clicked", "right": "Right-clicked",
            "middle": "Middle-clicked", "double": "Double-clicked",
        }.get(button, "Clicked")
        return ToolResult(success=True, output=f"{verb}{where} at {target_x}, {target_y}, sir.")

    @staticmethod
    def _do_click(pyautogui, x: int, y: int, button: str, clicks: int) -> None:
        if button == "double":
            pyautogui.doubleClick(x, y)
        elif button in ("left", "right", "middle"):
            pyautogui.click(x, y, clicks=clicks, button=button)
        else:
            pyautogui.click(x, y, clicks=clicks)


# ---------------------------------------------------------------------------
# 2. Move
# ---------------------------------------------------------------------------

class MouseMoveArgs(BaseModel):
    x: int | None = Field(default=None, description="X coordinate.")
    y: int | None = Field(default=None, description="Y coordinate.")
    text: str | None = Field(default=None, description="Move to on-screen text (OCR).")
    duration: float = Field(default=0.3, ge=0.0, le=5.0, description="Animation seconds.")


class MouseMoveTool:
    """Move the mouse cursor to coordinates or on-screen text."""

    name: str = "mouse_move"
    description: str = (
        "Move the mouse cursor to (x, y) or to on-screen text (OCR). "
        "(RU: перемести мышь / курсор на … / UK: пересунь мишу на …)"
    )
    args_schema: type[BaseModel] = MouseMoveArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^move\s+(?:the\s+)?(?:mouse|cursor)\s+(?:to\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2))},
        ),
        VoicePattern(
            regex=r"^move\s+(?:the\s+)?(?:mouse|cursor)\s+to\s+(.+)$",
            priority=240,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:перемести|передвинь)\s+(?:мышь|курсор)\s+(?:на\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2))},
        ),
        VoicePattern(
            regex=r"^курсор\s+(?:на\s+|к\s+)(.+)$",
            priority=240,
            args=lambda m: {"text": m.group(1)},
        ),
        VoicePattern(
            regex=r"^пересунь\s+(?:мишу|курсор)\s+(?:на\s+)?(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {"x": int(m.group(1)), "y": int(m.group(2))},
        ),
    )

    async def execute(self, args: MouseMoveArgs) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult(success=False, error="Mouse control needs pyautogui, sir.")
        tx, ty = args.x, args.y
        where = ""
        if args.text:
            found = await asyncio.to_thread(_find_text_on_screen, args.text)
            if found is None:
                return ToolResult(success=False, error=f"Couldn't find '{args.text}', sir.")
            tx, ty, _conf = found
            where = f" to '{args.text}'"
        if tx is None or ty is None:
            return ToolResult(success=False, error="Give coordinates or text, sir.")
        try:
            await asyncio.to_thread(pyautogui.moveTo, tx, ty, args.duration)
        except Exception as e:
            log.exception("mouse_move failed")
            return ToolResult(success=False, error=f"Move failed: {e}")
        log.info("[mouse] move to (%s,%s)%s", tx, ty, where)
        return ToolResult(success=True, output=f"Moved cursor{where} to {tx}, {ty}, sir.")


# ---------------------------------------------------------------------------
# 3. Scroll
# ---------------------------------------------------------------------------

class MouseScrollArgs(BaseModel):
    direction: str = Field(description="up, down, left, or right.")
    amount: int = Field(default=3, ge=1, le=100, description="Wheel clicks.")
    x: int | None = Field(default=None, description="Optional X to scroll at.")
    y: int | None = Field(default=None, description="Optional Y to scroll at.")


class MouseScrollTool:
    """Scroll the mouse wheel up/down (or horizontally)."""

    name: str = "mouse_scroll"
    description: str = (
        "Scroll the mouse wheel up, down, left, or right. "
        "(RU: прокрути вверх/вниз / UK: прокрути вгору/вниз)"
    )
    args_schema: type[BaseModel] = MouseScrollArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN with amount
        VoicePattern(
            regex=r"^scroll\s+(up|down|left|right)\s+(\d{1,3})(?:\s+times)?$",
            priority=230,
            args=lambda m: {"direction": m.group(1), "amount": int(m.group(2))},
        ),
        VoicePattern(
            regex=r"^scroll\s+(?:to\s+the\s+)?(up|down|top|bottom)$",
            priority=235,
            args=lambda m: {
                "direction": {"top": "up", "bottom": "down"}.get(m.group(1), m.group(1)),
                "amount": 50 if m.group(1) in ("top", "bottom") else 3,
            },
        ),
        # RU
        VoicePattern(
            regex=r"^прокрути\s+(вверх|вниз|влево|вправо)\s+(\d{1,3})(?:\s+раз[а]?)?$",
            priority=230,
            args=lambda m: {
                "direction": {"вверх": "up", "вниз": "down", "влево": "left", "вправо": "right"}[
                    m.group(1)
                ],
                "amount": int(m.group(2)),
            },
        ),
        VoicePattern(
            regex=r"^прокрути\s+(?:в\s+самый\s+)?(вверх|вниз|низ|верх)$",
            priority=235,
            args=lambda m: {
                "direction": {"вверх": "up", "верх": "up", "вниз": "down", "низ": "down"}[
                    m.group(1)
                ],
                "amount": 50 if m.group(1) in ("низ", "верх") else 3,
            },
        ),
        # UK
        VoicePattern(
            regex=r"^прокрути\s+(вгору|вниз|вліво|вправо)\s+(\d{1,3})(?:\s+раз[иів]?)?$",
            priority=230,
            args=lambda m: {
                "direction": {"вгору": "up", "вниз": "down", "вліво": "left", "вправо": "right"}[
                    m.group(1)
                ],
                "amount": int(m.group(2)),
            },
        ),
        VoicePattern(
            regex=r"^прокрути\s+(вгору|вниз)$",
            priority=236,
            args=lambda m: {"direction": {"вгору": "up", "вниз": "down"}[m.group(1)], "amount": 3},
        ),
        # EN plain (lowest of the scroll group so amount/top variants win)
        VoicePattern(
            regex=r"^scroll\s+(up|down|left|right)$",
            priority=238,
            args=lambda m: {"direction": m.group(1), "amount": 3},
        ),
    )

    async def execute(self, args: MouseScrollArgs) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult(success=False, error="Mouse control needs pyautogui, sir.")
        direction = args.direction.lower()
        if direction not in ("up", "down", "left", "right"):
            return ToolResult(success=False, error=f"Unknown direction '{args.direction}', sir.")
        try:
            await asyncio.to_thread(
                self._do_scroll, pyautogui, direction, args.amount, args.x, args.y
            )
        except Exception as e:
            log.exception("mouse_scroll failed")
            return ToolResult(success=False, error=f"Scroll failed: {e}")
        log.info("[mouse] scroll %s x%d", direction, args.amount)
        return ToolResult(success=True, output=f"Scrolled {direction}, sir.")

    @staticmethod
    def _do_scroll(pyautogui, direction: str, amount: int, x, y) -> None:
        # pyautogui.scroll: positive = up, negative = down. One "click" ≈ 100 units.
        clicks = amount * 100
        if direction in ("up", "down"):
            delta = clicks if direction == "up" else -clicks
            if x is not None and y is not None:
                pyautogui.scroll(delta, x=x, y=y)
            else:
                pyautogui.scroll(delta)
        else:  # left / right — hscroll where supported
            delta = clicks if direction == "right" else -clicks
            hscroll = getattr(pyautogui, "hscroll", None)
            if hscroll is not None:
                hscroll(delta)
            else:
                pyautogui.scroll(0)  # no-op fallback


# ---------------------------------------------------------------------------
# 4. Drag
# ---------------------------------------------------------------------------

class MouseDragArgs(BaseModel):
    from_x: int = Field(description="Start X.")
    from_y: int = Field(description="Start Y.")
    to_x: int = Field(description="End X.")
    to_y: int = Field(description="End Y.")
    duration: float = Field(default=0.5, ge=0.0, le=5.0, description="Drag seconds.")
    button: str = Field(default="left", description="Button to hold.")


class MouseDragTool:
    """Drag-and-drop from one point to another."""

    name: str = "mouse_drag"
    description: str = (
        "Drag the mouse from one point to another (drag-and-drop). "
        "(RU: перетащи с X Y на X Y / UK: перетягни з X Y на X Y)"
    )
    args_schema: type[BaseModel] = MouseDragArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^drag\s+(?:from\s+)?(\d{1,5})\s+(\d{1,5})\s+to\s+(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {
                "from_x": int(m.group(1)), "from_y": int(m.group(2)),
                "to_x": int(m.group(3)), "to_y": int(m.group(4)),
            },
        ),
        VoicePattern(
            regex=r"^перетащи\s+(?:с\s+|из\s+)?(\d{1,5})\s+(\d{1,5})\s+на\s+(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {
                "from_x": int(m.group(1)), "from_y": int(m.group(2)),
                "to_x": int(m.group(3)), "to_y": int(m.group(4)),
            },
        ),
        VoicePattern(
            regex=r"^перетягни\s+(?:з\s+)?(\d{1,5})\s+(\d{1,5})\s+на\s+(\d{1,5})\s+(\d{1,5})$",
            priority=230,
            args=lambda m: {
                "from_x": int(m.group(1)), "from_y": int(m.group(2)),
                "to_x": int(m.group(3)), "to_y": int(m.group(4)),
            },
        ),
    )

    async def execute(self, args: MouseDragArgs) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult(success=False, error="Mouse control needs pyautogui, sir.")
        button = args.button.lower()
        try:
            await asyncio.to_thread(self._do_drag, pyautogui, args, button)
        except Exception as e:
            log.exception("mouse_drag failed")
            return ToolResult(success=False, error=f"Drag failed: {e}")
        log.info(
            "[mouse] drag (%s,%s)->(%s,%s)",
            args.from_x, args.from_y, args.to_x, args.to_y,
        )
        return ToolResult(
            success=True,
            output=(
                f"Dragged from {args.from_x},{args.from_y} to "
                f"{args.to_x},{args.to_y}, sir."
            ),
        )

    @staticmethod
    def _do_drag(pyautogui, args: MouseDragArgs, button: str) -> None:
        pyautogui.moveTo(args.from_x, args.from_y)
        pyautogui.dragTo(args.to_x, args.to_y, duration=args.duration, button=button)


# ---------------------------------------------------------------------------
# 5. Position
# ---------------------------------------------------------------------------

class MousePositionArgs(BaseModel):
    pass


class MousePositionTool:
    """Report the current mouse cursor position."""

    name: str = "mouse_position"
    description: str = (
        "Report the current mouse cursor coordinates. "
        "(RU: где мышь / позиция курсора — UK: де миша / позиція курсора)"
    )
    args_schema: type[BaseModel] = MousePositionArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.READ

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(regex=r"^(?:where\s+is\s+(?:the\s+)?(?:mouse|cursor)"
                           r"|(?:mouse|cursor)\s+position)$", priority=230),
        VoicePattern(regex=r"^(?:где\s+(?:мышь|курсор)|позиция\s+(?:мыши|курсора))$",
                     priority=230),
        VoicePattern(regex=r"^(?:де\s+(?:миша|курсор)|позиція\s+(?:миші|курсора))$",
                     priority=230),
    )

    async def execute(self, args: MousePositionArgs) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult(success=False, error="Mouse control needs pyautogui, sir.")
        try:
            pos = await asyncio.to_thread(pyautogui.position)
        except Exception as e:
            return ToolResult(success=False, error=f"Could not read position: {e}")
        return ToolResult(success=True, output=f"The cursor is at {pos[0]}, {pos[1]}, sir.")
