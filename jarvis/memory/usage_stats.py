"""Usage statistics — tracks command frequency and patterns.

Tracks:
  - Top apps opened (which 5 apps user opens most)
  - Top commands used (which tools are called most)
  - Time patterns (morning/afternoon/evening activity)

Used for:
  - Faster suggestions
  - Priority in auto_recall (frequently used = more relevant)
  - Future: proactive suggestions ("Обычно в это время вы открываете Steam")

Storage: %APPDATA%/Jarvis/usage_stats.json
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)


class UsageStats:
    """Persistent usage statistics — tracks command and app frequency."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = EncryptedJsonStore(path)
        self._data: dict = {"apps": {}, "tools": {}, "daily_hours": {}}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def record_tool_use(self, tool_name: str) -> None:
        """Record that a tool was used.

        Safe to call from any context — never raises.
        """
        try:
            tools = self._data.setdefault("tools", {})
            tools[tool_name] = tools.get(tool_name, 0) + 1

            # Track hour of day
            hour = str(datetime.now().hour)
            hours = self._data.setdefault("daily_hours", {})
            hours[hour] = hours.get(hour, 0) + 1

            self._save()
        except Exception:
            log.warning("[usage-stats] record_tool_use failed", exc_info=True)

    def record_app_use(self, app_name: str) -> None:
        """Record that an app was opened.

        Safe to call from any context — never raises.
        """
        try:
            apps = self._data.setdefault("apps", {})
            key = app_name.strip().lower()
            if not key:
                return
            apps[key] = apps.get(key, 0) + 1
            self._save()
        except Exception:
            log.warning("[usage-stats] record_app_use failed", exc_info=True)

    def top_apps(self, n: int = 5) -> list[tuple[str, int]]:
        """Top N most opened apps."""
        return Counter(self._data.get("apps", {})).most_common(n)

    def top_tools(self, n: int = 5) -> list[tuple[str, int]]:
        """Top N most used tools."""
        return Counter(self._data.get("tools", {})).most_common(n)

    def active_hours(self) -> list[int]:
        """Hours when user is most active (sorted by frequency)."""
        hours = self._data.get("daily_hours", {})
        return [int(h) for h, _ in Counter(hours).most_common(5)]

    def for_prompt(self) -> str | None:
        """Format stats as context hint for LLM.

        Returns None when no app usage data is available.
        """
        top = self.top_apps(3)
        if not top:
            return None
        apps_str = ", ".join(f"{name} ({count}×)" for name, count in top)
        return f"[User's frequently used apps: {apps_str}]"

    def _load(self) -> None:
        try:
            loaded = self._store.load(default={"apps": {}, "tools": {}, "daily_hours": {}})
            if isinstance(loaded, dict):
                self._data = loaded
        except Exception:
            log.warning(
                "[usage-stats] failed to load %s; starting fresh",
                self._path,
                exc_info=True,
            )

    def _save(self) -> None:
        try:
            self._store.save(self._data)
        except Exception:
            log.warning(
                "[usage-stats] failed to save %s",
                self._path,
                exc_info=True,
            )
