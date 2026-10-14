"""System volume control via the Windows media keys.

Granularity is one Windows volume tick (~2%). The `amount` arg is a
multiplier on that tick — `amount=5` is roughly a 10% step. We
deliberately avoid pycaw / Core Audio APIs to keep the dependency
footprint flat; the media-key path is what hardware multimedia
keyboards trigger and behaves identically."""

from __future__ import annotations

import asyncio
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.platform import windows as winplat
from jarvis.tools.registry import ToolResult, VoicePattern


class VolumeArgs(BaseModel):
    action: Literal["up", "down", "mute", "unmute"] = Field(
        description="Direction: up, down, mute, or unmute."
    )
    amount: int = Field(
        default=5, ge=1, le=20,
        description=(
            "Number of volume ticks for up/down (ignored for mute/unmute). "
            "Each tick ~2 percent."
        ),
    )


# -- RU/UK voice-action word → EN action mappings for volume patterns ---

_VOLUME_ACTION_MAP_RU: dict[str, str] = {
    "громче": "up",
    "тише": "down",
    "выключи": "mute",
    "выключить": "mute",
    "включи": "unmute",
    "включить": "unmute",
}

_VOLUME_ACTION_MAP_UK: dict[str, str] = {
    "гучніше": "up",
    "тихіше": "down",
    "вимкни": "mute",
    "вимкнути": "mute",
    "увімкни": "unmute",
    "увімкнути": "unmute",
}


class VolumeTool:
    name: str = "volume"
    description: str = (
        "Changes system audio volume. Only use when the user explicitly "
        "asks to change volume. action=up|down|mute|unmute; amount sets "
        "the step count for up/down (1-20, ~2% per tick)."
    )
    args_schema = VolumeArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^volume\s+(up|down|mute|unmute)$",
            priority=10,
            args=lambda m: {"action": m.group(1)},
        ),
        # RU: "звук громче", "громкость тише", "звук выключи"
        VoicePattern(
            regex=r"^(?:звук|громкость)\s+(громче|тише|выключи(?:ть)?|включи(?:ть)?)$",
            priority=10,
            args=lambda m: {"action": _VOLUME_ACTION_MAP_RU.get(m.group(1), "up")},
        ),
        # UK: "звук гучніше", "гучність тихіше", "звук вимкни"
        VoicePattern(
            regex=r"^(?:звук|гучність)\s+(гучніше|тихіше|вимкн(?:и|ути)|увімкн(?:и|ути))$",
            priority=10,
            args=lambda m: {"action": _VOLUME_ACTION_MAP_UK.get(m.group(1), "up")},
        ),
    )

    async def execute(self, args: VolumeArgs) -> ToolResult:
        try:
            if args.action == "up":
                await asyncio.to_thread(winplat.volume_up, args.amount)
                spoken = "Volume up, sir."
            elif args.action == "down":
                await asyncio.to_thread(winplat.volume_down, args.amount)
                spoken = "Volume down, sir."
            else:
                # mute and unmute both tap the toggle key. The OS owns
                # the actual mute state; we don't second-guess it.
                await asyncio.to_thread(winplat.volume_mute_toggle)
                spoken = "Toggled mute, sir."
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(success=True, output=spoken)
