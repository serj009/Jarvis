"""User profile — persistent facts about the user.

Stored as a structured JSON, injected into every LLM call as part
of the system context. Contains:
  - name: how to address the user
  - city: for weather defaults
  - language: preferred language
  - timezone: for time formatting
  - custom fields: anything the user tells about themselves

Auto-populated from:
  - auto_save facts tagged "identity", "location"
  - Explicit: "меня зовут Серж", "я живу в Зелёна Гура"

Storage: %APPDATA%/Jarvis/user_profile.json
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PROFILE: dict = {
    "name": None,        # "Serhii", "Серж"
    "city": None,        # "Zielona Góra"
    "country": None,     # "Poland"
    "language": "auto",  # preferred response language
    "timezone": None,    # "Europe/Warsaw"
    "occupation": None,  # "works at Amazon"
    "interests": [],     # ["gaming", "programming", "Jarvis"]
    "custom": {},        # any other key-value facts
}


class UserProfile:
    """Persistent user profile — structured facts injected into LLM context."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict = {k: (list(v) if isinstance(v, list) else
                                dict(v) if isinstance(v, dict) else v)
                            for k, v in DEFAULT_PROFILE.items()}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf-8"))
                self._data.update(saved)
            except (json.JSONDecodeError, OSError):
                log.warning(
                    "[user-profile] failed to load %s; using defaults",
                    self._path,
                    exc_info=True,
                )

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            log.warning(
                "[user-profile] failed to save %s",
                self._path,
                exc_info=True,
            )

    def get(self, key: str):
        """Get a profile field. Checks top-level keys first, then custom."""
        val = self._data.get(key)
        if val is not None:
            return val
        return self._data.get("custom", {}).get(key)

    def set(self, key: str, value) -> None:
        """Set a profile field. Known keys go top-level, others into custom."""
        if key in DEFAULT_PROFILE:
            self._data[key] = value
        else:
            self._data.setdefault("custom", {})[key] = value
        self._save()

    def update_from_fact(self, fact_text: str, category: str) -> None:
        """Auto-update profile from extracted facts.

        Called by auto_save after saving an identity/location fact.
        Safe to call from any context — never raises.
        """
        try:
            self._update_from_fact_inner(fact_text, category)
        except Exception:
            log.warning("[user-profile] update_from_fact failed", exc_info=True)

    def _update_from_fact_inner(self, fact_text: str, category: str) -> None:
        text = fact_text.lower()

        # Name detection
        for pattern in [
            r"(?:меня зовут|my name is|мене звати)\s+(\w+)",
            r"(?:зови меня|call me)\s+(\w+)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                self.set("name", m.group(1).capitalize())
                log.info("[user-profile] learned name: %s", m.group(1).capitalize())
                return

        # City detection
        for pattern in [
            r"(?:я живу в|i live in)\s+(.+)",
            r"(?:я из|i'm from|я з)\s+(.+)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                city = m.group(1).strip().rstrip(".,!?").title()
                self.set("city", city)
                log.info("[user-profile] learned city: %s", city)
                return

        # Occupation detection
        for pattern in [
            r"(?:i work (?:at|for|as)|я работаю|я працюю)\s+(.+)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                occupation = m.group(1).strip().rstrip(".,!?")
                self.set("occupation", occupation)
                log.info("[user-profile] learned occupation: %s", occupation)
                return

    def for_prompt(self) -> str | None:
        """Format profile for injection into LLM system context.

        Returns None if profile is empty (no known facts).
        """
        parts: list[str] = []
        if self._data.get("name"):
            parts.append(f"User's name: {self._data['name']}")
        if self._data.get("city"):
            loc = self._data["city"]
            if self._data.get("country"):
                loc += f", {self._data['country']}"
            parts.append(f"Location: {loc}")
        if self._data.get("timezone"):
            parts.append(f"Timezone: {self._data['timezone']}")
        if self._data.get("occupation"):
            parts.append(f"Occupation: {self._data['occupation']}")
        if self._data.get("interests"):
            interests = self._data["interests"]
            parts.append(f"Interests: {', '.join(interests[:5])}")

        custom = self._data.get("custom", {})
        for k, v in list(custom.items())[:5]:
            parts.append(f"{k}: {v}")

        if not parts:
            return None
        return "[User profile:]\n" + "\n".join(parts)
