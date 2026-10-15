"""Personal aliases — Jarvis learns user's naming conventions.

Static aliases (app_aliases.py) cover common names like "хром"→"chrome".
Personal aliases cover user-specific shortcuts:
  - "браузер" → "chrome" (user always means Chrome)
  - "редактор" → "vscode" (user's preferred editor)
  - "мой проект" → specific path or app

Storage: JSON file at %APPDATA%/Jarvis/personal_aliases.json
Format: {
    "браузер": {"target": "chrome", "count": 15, "last_used": "..."},
    "редактор": {"target": "code", "count": 8, "last_used": "..."},
}

Learning sources:
  1. Explicit: "мой браузер — Chrome" → save alias
  2. Implicit: tool_examples shows "браузер"→open_app("chrome") succeeded 5 times
  3. Correction: user says "нет, открой Edge" after "открой браузер"→Chrome → update alias
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)

MAX_ALIASES = 200


class PersonalAliasStore:
    """Persistent store for user-learned application aliases."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._store = EncryptedJsonStore(path)
        self._data: dict[str, dict] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        try:
            self._data = self._store.load(default={})
        except Exception:
            log.warning(
                "[personal-aliases] failed to load %s; starting fresh",
                self._path,
                exc_info=True,
            )
            self._data = {}

    def _save(self) -> None:
        try:
            self._store.save(self._data)
        except Exception:
            log.warning(
                "[personal-aliases] failed to save %s",
                self._path,
                exc_info=True,
            )

    def resolve(self, name: str) -> str | None:
        """Lookup personal alias. Returns target or None."""
        key = name.strip().lower()
        if not key:
            return None
        entry = self._data.get(key)
        if entry:
            entry["count"] = entry.get("count", 0) + 1
            entry["last_used"] = datetime.now(UTC).isoformat()
            self._save()
            return entry["target"]
        return None

    def learn(self, alias: str, target: str) -> None:
        """Add or update a personal alias.

        Safe to call from any context — never raises.
        """
        try:
            self._learn_inner(alias, target)
        except Exception:
            log.warning("[personal-aliases] learn failed", exc_info=True)

    def _learn_inner(self, alias: str, target: str) -> None:
        key = alias.strip().lower()
        target_clean = target.strip().lower()
        if not key or not target_clean:
            return

        existing = self._data.get(key)
        if existing and existing.get("target") == target_clean:
            # Same mapping — just bump count
            existing["count"] = existing.get("count", 0) + 1
            existing["last_used"] = datetime.now(UTC).isoformat()
        else:
            # New or changed mapping
            self._data[key] = {
                "target": target_clean,
                "count": 1,
                "last_used": datetime.now(UTC).isoformat(),
            }

        # Prune if too many — remove least used
        if len(self._data) > MAX_ALIASES:
            sorted_keys = sorted(
                self._data.keys(),
                key=lambda k: self._data[k].get("count", 0),
            )
            for k in sorted_keys[: len(self._data) - MAX_ALIASES]:
                del self._data[k]

        self._save()

    def get_all(self) -> dict[str, str]:
        """All aliases as {alias: target}."""
        return {k: v["target"] for k, v in self._data.items()}

    def alias_count(self) -> int:
        """Total number of stored aliases."""
        return len(self._data)
