"""Focus Mode — block distracting websites for a set duration.

Two implementation strategies:

1. **Hosts-file mode** (requires admin/root): writes ``127.0.0.1 <domain>``
   entries into the system hosts file. This blocks the sites at the OS
   level, affecting all browsers and apps.

2. **Soft mode** (no admin required, default): stores the blocked list
   and active timer in ``~/.jarvis/data/focus_mode.json``. Jarvis will
   refuse to open blocked sites while focus mode is active. Does not
   physically block access — relies on Jarvis being the browsing proxy.

The tool automatically detects admin privileges and falls back to soft
mode when hosts-file writing is not possible.

State file: ``~/.jarvis/data/focus_mode.json``
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.platform.encrypted_store import EncryptedJsonStore
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_FOCUS_STORE: EncryptedJsonStore | None = None


def _store() -> EncryptedJsonStore:
    # Lazily build the store so the data dir is resolved at call time via the
    # single resolver (jarvis.paths); layout unchanged: <data_dir>/data/focus_mode.json.
    global _FOCUS_STORE
    if _FOCUS_STORE is None:
        from jarvis.paths import data_path

        _FOCUS_STORE = EncryptedJsonStore(data_path("data", "focus_mode.json"))
    return _FOCUS_STORE

# Default distraction sites
_DEFAULT_BLOCKED: list[str] = [
    "youtube.com", "www.youtube.com",
    "reddit.com", "www.reddit.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "tiktok.com", "www.tiktok.com",
    "instagram.com", "www.instagram.com",
    "facebook.com", "www.facebook.com",
]

# Hosts file path per platform
_HOSTS_PATH = (
    Path(r"C:\Windows\System32\drivers\etc\hosts")
    if sys.platform == "win32"
    else Path("/etc/hosts")
)

_FOCUS_MARKER = "# JARVIS-FOCUS-MODE"


def _load_state() -> dict[str, Any]:
    """Load focus mode state from disk."""
    return _store().load(
        default={"active": False, "blocked": [], "end_time": None, "mode": "soft"}
    )


def _save_state(state: dict[str, Any]) -> None:
    _store().save(state)


def _is_expired(state: dict[str, Any]) -> bool:
    """Check if the focus session has expired."""
    end_time = state.get("end_time")
    if end_time is None:
        return False
    return time.time() > end_time


def _has_admin() -> bool:
    """Check if we have privileges to modify the hosts file."""
    try:
        if sys.platform == "win32":
            from jarvis import platform as platform_ops
            return platform_ops.is_user_admin()
        else:
            import os
            return os.geteuid() == 0
    except Exception:
        return False


def _add_hosts_entries(domains: list[str]) -> str | None:
    """Add blocking entries to hosts file. Returns error or None."""
    try:
        content = _HOSTS_PATH.read_text(encoding="utf-8")
        lines = [f"127.0.0.1 {d}  {_FOCUS_MARKER}" for d in domains]
        new_content = content.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
        _HOSTS_PATH.write_text(new_content, encoding="utf-8")
        return None
    except PermissionError:
        return "Permission denied. Run as administrator to modify hosts file."
    except Exception as exc:
        return f"Failed to modify hosts file: {exc}"


def _remove_hosts_entries() -> str | None:
    """Remove Jarvis focus mode entries from hosts file."""
    try:
        content = _HOSTS_PATH.read_text(encoding="utf-8")
        lines = [
            line for line in content.splitlines()
            if _FOCUS_MARKER not in line
        ]
        _HOSTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return None
    except PermissionError:
        return "Permission denied. Run as administrator."
    except Exception as exc:
        return f"Failed to clean hosts file: {exc}"


def is_site_blocked(url: str) -> bool:
    """Public API: check if a URL's domain is currently focus-blocked.

    Used by other Jarvis tools (e.g. open_url) to refuse opening
    blocked sites during focus mode.
    """
    state = _load_state()
    if not state.get("active", False):
        return False
    if _is_expired(state):
        # Auto-expire
        _deactivate_focus(state)
        return False
    blocked = state.get("blocked", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in blocked)


def _deactivate_focus(state: dict[str, Any]) -> None:
    """Deactivate focus mode and clean up."""
    if state.get("mode") == "hosts":
        _remove_hosts_entries()
    state["active"] = False
    state["end_time"] = None
    _save_state(state)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class StartFocusModeArgs(BaseModel):
    duration_minutes: int = Field(
        default=60,
        description="Focus session duration in minutes (1-480, default 60).",
        ge=1,
        le=480,
    )
    sites: list[str] = Field(
        default_factory=list,
        description=(
            "Custom list of domains to block. If empty, uses default list: "
            "youtube, reddit, twitter, tiktok, instagram, facebook."
        ),
    )
    use_hosts: bool = Field(
        default=False,
        description=(
            "If True, attempt to modify the hosts file (requires admin). "
            "If False (default), use soft mode."
        ),
    )


class StopFocusModeArgs(BaseModel):
    """No arguments needed."""
    pass


class FocusModeStatusArgs(BaseModel):
    """No arguments needed."""
    pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class StartFocusModeTool:
    name: str = "start_focus_mode"
    description: str = (
        "Starts Focus Mode — blocks distracting websites for a set duration. "
        "Default blocks: YouTube, Reddit, Twitter, TikTok, Instagram, Facebook. "
        "Soft mode: Jarvis refuses to open blocked sites. "
        "Hosts mode (admin): blocks at OS level. "
        "(RU: режим фокуса, заблокируй ютуб на 2 часа / "
        "UK: режим фокусу, заблокуй ютуб на 2 години)"
    )
    args_schema = StartFocusModeArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:focus|work)\s+mode\s+(?:on|start)(?:\s+(?:for\s+)?(\d+)\s+(?:min(?:ute)?s?|hours?))?$",
            priority=450,
            args=lambda m: {
                "duration_minutes": (
                    int(m.group(1)) * (60 if "hour" in (m.group(0) or "") else 1)
                ) if m.group(1) else 60,
            },
        ),
        VoicePattern(
            regex=r"^(?:start\s+)?focus\s+mode$",
            priority=450,
            args=lambda m: {"duration_minutes": 60},
        ),
        VoicePattern(
            regex=r"^block\s+(.+?)\s+for\s+(\d+)\s+(min(?:ute)?s?|hours?)$",
            priority=450,
            args=lambda m: {
                "sites": [s.strip() for s in m.group(1).split(",")],
                "duration_minutes": (
                    int(m.group(2)) * 60 if "hour" in m.group(3) else int(m.group(2))
                ),
            },
        ),
        # RU
        VoicePattern(
            regex=r"^режим\s+(?:фокуса|работы)(?:\s+на\s+(\d+)\s+(?:минут\w*|час\w*))?$",
            priority=450,
            args=lambda m: {
                "duration_minutes": (
                    int(m.group(1)) * (60 if "час" in (m.group(0) or "") else 1)
                ) if m.group(1) else 60,
            },
        ),
        VoicePattern(
            regex=r"^заблокируй\s+(.+?)\s+на\s+(\d+)\s+(минут\w*|час\w*)$",
            priority=450,
            args=lambda m: {
                "sites": [s.strip() for s in m.group(1).split(",")],
                "duration_minutes": (
                    int(m.group(2)) * 60 if "час" in m.group(3) else int(m.group(2))
                ),
            },
        ),
        # UK
        VoicePattern(
            regex=r"^режим\s+(?:фокусу|роботи)(?:\s+на\s+(\d+)\s+(?:хвилин\w*|годин\w*))?$",
            priority=450,
            args=lambda m: {
                "duration_minutes": (
                    int(m.group(1)) * (60 if "годин" in (m.group(0) or "") else 1)
                ) if m.group(1) else 60,
            },
        ),
        VoicePattern(
            regex=r"^заблокуй\s+(.+?)\s+на\s+(\d+)\s+(хвилин\w*|годин\w*)$",
            priority=450,
            args=lambda m: {
                "sites": [s.strip() for s in m.group(1).split(",")],
                "duration_minutes": (
                    int(m.group(2)) * 60 if "годин" in m.group(3) else int(m.group(2))
                ),
            },
        ),
    )

    async def execute(self, args: StartFocusModeArgs) -> ToolResult:
        blocked = args.sites if args.sites else list(_DEFAULT_BLOCKED)
        end_time = time.time() + args.duration_minutes * 60
        mode = "soft"

        if args.use_hosts:
            if _has_admin():
                err = await asyncio.to_thread(_add_hosts_entries, blocked)
                if err:
                    return ToolResult(success=False, error=err)
                mode = "hosts"
            else:
                log.info("No admin privileges — falling back to soft mode")

        state = {
            "active": True,
            "blocked": blocked,
            "end_time": end_time,
            "mode": mode,
            "started_at": datetime.now(UTC).isoformat(),
            "duration_minutes": args.duration_minutes,
        }
        await asyncio.to_thread(_save_state, state)

        mode_label = "hosts-file" if mode == "hosts" else "soft"
        domains_preview = ", ".join(blocked[:5])
        if len(blocked) > 5:
            domains_preview += f" (+{len(blocked) - 5} more)"

        return ToolResult(
            success=True,
            output=(
                f"🎯 Focus mode ON ({mode_label}) for {args.duration_minutes} minutes.\n"
                f"Blocked: {domains_preview}"
            ),
        )


class StopFocusModeTool:
    name: str = "stop_focus_mode"
    description: str = (
        "Stops Focus Mode and unblocks all sites. "
        "(RU: выключи фокус, сними блокировку / "
        "UK: вимкни фокус, зніми блокування)"
    )
    args_schema = StopFocusModeArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:stop|end|disable|turn\s+off)\s+focus\s+mode$",
            priority=450,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^focus\s+mode\s+off$",
            priority=450,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:выключи|останови|отключи)\s+(?:режим\s+)?фокус(?:а)?$",
            priority=450,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:вимкни|зупини|відключи)\s+(?:режим\s+)?фокус(?:у)?$",
            priority=450,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: StopFocusModeArgs) -> ToolResult:
        state = await asyncio.to_thread(_load_state)
        if not state.get("active", False):
            return ToolResult(success=True, output="Focus mode is not active.")

        await asyncio.to_thread(_deactivate_focus, state)
        return ToolResult(
            success=True,
            output="🔓 Focus mode OFF. All sites unblocked.",
        )


class FocusModeStatusTool:
    name: str = "focus_mode_status"
    description: str = (
        "Shows the current Focus Mode status: active/inactive, blocked sites, "
        "and remaining time. "
        "(RU: статус фокуса / UK: статус фокусу)"
    )
    args_schema = FocusModeStatusArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:focus|work)\s+mode\s+status$",
            priority=450,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:is\s+)?focus\s+mode\s+(?:on|active)\??$",
            priority=450,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:статус|состояние)\s+(?:режима?\s+)?фокуса?$",
            priority=450,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:статус|стан)\s+(?:режиму?\s+)?фокусу?$",
            priority=450,
            args=lambda m: {},
        ),
    )

    async def execute(self, args: FocusModeStatusArgs) -> ToolResult:
        state = await asyncio.to_thread(_load_state)

        if not state.get("active", False):
            return ToolResult(success=True, output="Focus mode is not active.")

        if _is_expired(state):
            await asyncio.to_thread(_deactivate_focus, state)
            return ToolResult(
                success=True,
                output="Focus mode session has ended. Sites are unblocked.",
            )

        end_time = state.get("end_time", 0)
        remaining = max(0, end_time - time.time())
        mins_left = int(remaining // 60)
        mode = state.get("mode", "soft")
        blocked = state.get("blocked", [])

        domains_preview = ", ".join(blocked[:5])
        if len(blocked) > 5:
            domains_preview += f" (+{len(blocked) - 5} more)"

        return ToolResult(
            success=True,
            output=(
                f"🎯 Focus mode ACTIVE ({mode} mode)\n"
                f"Time remaining: {mins_left} minute(s)\n"
                f"Blocked: {domains_preview}"
            ),
        )
