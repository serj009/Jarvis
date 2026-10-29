"""Power control — shutdown, restart, sleep, hibernate, caffeinate.

ALL destructive power operations (shutdown, restart, hibernate)
require user confirmation via the risk system (SYSTEM risk level).
Sleep and caffeinate are WRITE-level (reversible).
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UAC / elevation helpers
# ---------------------------------------------------------------------------

_UAC_MSG = (
    "I can't do that without administrator privileges, sir. "
    "Try running JARVIS as admin, or do it manually."
)


def _is_access_denied(exc: OSError) -> bool:
    """Return True if *exc* is a Windows ERROR_ACCESS_DENIED (winerror 5)."""
    return getattr(exc, "winerror", None) == 5



class PowerArgs(BaseModel):
    action: Literal[
        "shutdown", "restart", "sleep", "hibernate",
        "caffeinate", "cancel_shutdown",
    ] = Field(description="Power action to perform.")
    delay_sec: int = Field(
        default=30, ge=0, le=3600,
        description=(
            "Delay in seconds before shutdown/restart (0–3600). "
            "Ignored for sleep/hibernate/caffeinate."
        ),
    )
    caffeinate_minutes: int = Field(
        default=60, ge=1, le=480,
        description="Minutes to prevent sleep (1–480). Only for caffeinate.",
    )


def _run_subprocess(cmd: list[str], timeout: int = 10) -> str:
    """Run a subprocess, return stdout. Raises on failure."""
    try:
        import subprocess
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"exit code {result.returncode}")
        return result.stdout.strip()
    except FileNotFoundError:
        raise NotImplementedError(f"Command not found: {cmd[0]}") from None


def _shutdown(delay: int) -> str:
    _run_subprocess(["shutdown", "/s", "/t", str(delay)])
    return f"Shutdown scheduled in {delay} seconds. Cancel with 'cancel shutdown'."


def _restart(delay: int) -> str:
    _run_subprocess(["shutdown", "/r", "/t", str(delay)])
    return f"Restart scheduled in {delay} seconds. Cancel with 'cancel shutdown'."


def _cancel_shutdown() -> str:
    _run_subprocess(["shutdown", "/a"])
    return "Shutdown/restart cancelled."


def _sleep() -> str:
    try:
        from jarvis import platform as platform_ops
        platform_ops.sleep_system()
    except (ImportError, AttributeError, OSError) as e:
        raise NotImplementedError(f"Sleep not available: {e}") from e
    return "Going to sleep."


def _hibernate() -> str:
    try:
        from jarvis import platform as platform_ops
        platform_ops.hibernate_system()
    except (ImportError, AttributeError, OSError) as e:
        raise NotImplementedError(f"Hibernate not available: {e}") from e
    return "Hibernating."


def _caffeinate(minutes: int) -> str:
    """Prevent sleep by setting thread execution state."""
    try:
        from jarvis import platform as platform_ops
        platform_ops.prevent_sleep()
    except (ImportError, AttributeError, OSError) as e:
        raise NotImplementedError(f"Caffeinate not available: {e}") from e
    return f"Preventing sleep for {minutes} minutes."


class PowerControlTool:
    name: str = "power_control"
    description: str = (
        "System power management: shutdown, restart, sleep, hibernate, "
        "caffeinate (prevent sleep), or cancel a scheduled shutdown. "
        "Shutdown and restart require confirmation. "
        "(RU: выключи компьютер, перезагрузка, спящий режим, не засыпай / "
        "UK: вимкни комп'ютер, перезавантаження, сплячий режим, не засинай)"
    )
    args_schema = PowerArgs
    # Confirmation handled via TOOL_RISK_MAP (SYSTEM level) in risk.py.
    # The risk system gates shutdown/restart/hibernate automatically.
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^shut\s*down(?:\s+(?:the\s+)?(?:pc|computer))?$",
            priority=50,
            args=lambda m: {"action": "shutdown"},
        ),
        VoicePattern(
            regex=r"^restart(?:\s+(?:the\s+)?(?:pc|computer))?$",
            priority=50,
            args=lambda m: {"action": "restart"},
        ),
        VoicePattern(
            regex=r"^(?:go\s+to\s+)?sleep$",
            priority=50,
            args=lambda m: {"action": "sleep"},
        ),
        VoicePattern(
            regex=r"^(?:don'?t|do\s+not)\s+(?:go\s+to\s+)?sleep$",
            priority=50,
            args=lambda m: {"action": "caffeinate"},
        ),
        VoicePattern(
            regex=r"^cancel\s+shutdown$",
            priority=50,
            args=lambda m: {"action": "cancel_shutdown"},
        ),
        # RU
        VoicePattern(
            regex=r"^выключи(?:\s+компьютер)?$",
            priority=50,
            args=lambda m: {"action": "shutdown"},
        ),
        VoicePattern(
            regex=r"^перезагруз(?:и|ка|ись)(?:\s+компьютер)?$",
            priority=50,
            args=lambda m: {"action": "restart"},
        ),
        VoicePattern(
            regex=r"^(?:спящий\s+режим|усни|засни)$",
            priority=50,
            args=lambda m: {"action": "sleep"},
        ),
        VoicePattern(
            regex=r"^не\s+засыпай$",
            priority=50,
            args=lambda m: {"action": "caffeinate"},
        ),
        VoicePattern(
            regex=r"^отмени\s+(?:выключение|перезагрузку)$",
            priority=50,
            args=lambda m: {"action": "cancel_shutdown"},
        ),
        # UK
        VoicePattern(
            regex=r"^вимкни(?:\s+комп'?ютер)?$",
            priority=50,
            args=lambda m: {"action": "shutdown"},
        ),
        VoicePattern(
            regex=r"^перезавантаж(?:ся|ення)?(?:\s+комп'?ютер)?$",
            priority=50,
            args=lambda m: {"action": "restart"},
        ),
        VoicePattern(
            regex=r"^(?:сплячий\s+режим|засни)$",
            priority=50,
            args=lambda m: {"action": "sleep"},
        ),
        VoicePattern(
            regex=r"^не\s+засинай$",
            priority=50,
            args=lambda m: {"action": "caffeinate"},
        ),
        VoicePattern(
            regex=r"^скасуй\s+(?:вимкнення|перезавантаження)$",
            priority=50,
            args=lambda m: {"action": "cancel_shutdown"},
        ),
    )

    async def execute(self, args: PowerArgs) -> ToolResult:
        try:
            action = args.action
            if action == "shutdown":
                msg = await asyncio.to_thread(_shutdown, args.delay_sec)
            elif action == "restart":
                msg = await asyncio.to_thread(_restart, args.delay_sec)
            elif action == "cancel_shutdown":
                msg = await asyncio.to_thread(_cancel_shutdown)
            elif action == "sleep":
                msg = await asyncio.to_thread(_sleep)
            elif action == "hibernate":
                msg = await asyncio.to_thread(_hibernate)
            elif action == "caffeinate":
                msg = await asyncio.to_thread(_caffeinate, args.caffeinate_minutes)
            else:
                return ToolResult(success=False, error=f"Unknown action: {action}")

            return ToolResult(success=True, output=msg)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            log.exception("power_control %s failed", args.action)
            return ToolResult(success=False, error=f"Power control failed: {e}")
        except Exception as e:
            log.exception("power_control %s failed", args.action)
            return ToolResult(success=False, error=f"Power control failed: {e}")
