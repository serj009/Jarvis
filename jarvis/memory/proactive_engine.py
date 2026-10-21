"""Proactive ("initiative") engine — Layer 1 foundation.

A background loop that watches usage patterns and occasionally speaks up on
its own via the existing ``ProactiveSpeech`` event. This module is the
foundation: initiative levels, the adaptive dismiss→downgrade mechanic, the
SQLite pattern store, and the foreground-app helper. The five concrete
scenarios (abandoned project, birthdays, long-work, boot routine, command
sequences) are added in Layer 2 as evaluation methods.

Design:
- Reuses ``EventBus`` + ``ProactiveSpeech`` — never a new speech mechanism.
- Reads ``UsageStats`` — never duplicates it.
- Own SQLite table ``proactive_patterns`` (CREATE IF NOT EXISTS, no ALTER).
- Initiative level persists in config, so it survives a restart.
- Fully local, zero new dependencies.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".jarvis" / "proactive_patterns.db"


# ---------------------------------------------------------------------------
# Initiative level
# ---------------------------------------------------------------------------

class InitiativeLevel(Enum):
    """How assertively Jarvis offers unprompted suggestions."""

    HIGH = "high"      # suggests actively (default)
    MEDIUM = "medium"  # only important things (projects, birthdays)
    LOW = "low"        # only critical (health, birthday today)
    SILENT = "silent"  # never speaks unprompted

    @classmethod
    def from_str(cls, value: str) -> "InitiativeLevel":
        try:
            return cls(value.strip().lower())
        except (ValueError, AttributeError):
            return cls.HIGH

    def downgraded(self) -> "InitiativeLevel":
        """Return the next quieter level (SILENT is the floor)."""
        order = [self.HIGH, self.MEDIUM, self.LOW, self.SILENT]
        idx = order.index(self)
        return order[min(idx + 1, len(order) - 1)]

    def upgraded(self) -> "InitiativeLevel":
        """Return the next louder level (HIGH is the ceiling)."""
        order = [self.HIGH, self.MEDIUM, self.LOW, self.SILENT]
        idx = order.index(self)
        return order[max(idx - 1, 0)]


# Which scenario kinds are allowed to speak at each level. Scenario
# importance tiers: "normal" (routine/sequence), "important" (project,
# birthday), "critical" (health, birthday-today).
_LEVEL_ALLOWS: dict[InitiativeLevel, set[str]] = {
    InitiativeLevel.HIGH: {"normal", "important", "critical"},
    InitiativeLevel.MEDIUM: {"important", "critical"},
    InitiativeLevel.LOW: {"critical"},
    InitiativeLevel.SILENT: set(),
}


# ---------------------------------------------------------------------------
# Foreground-app helper (Windows; empty off-Windows)
# ---------------------------------------------------------------------------

def foreground_window_title() -> str:
    """Best-effort Windows foreground window title. '' off Windows."""
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:  # noqa: BLE001
        return ""


GAME_INDICATORS = {"steam", "epic", "game", "play", "gog", "battle.net", "riot"}
VIDEO_INDICATORS = {"youtube", "netflix", "vlc", "mpv", "plex", "twitch", "kinopub"}


def classify_leisure(app_title: str) -> str | None:
    """Return 'game' / 'video' / None for a foreground window title."""
    if not app_title:
        return None
    low = app_title.lower()
    if any(tok in low for tok in GAME_INDICATORS):
        return "game"
    if any(tok in low for tok in VIDEO_INDICATORS):
        return "video"
    return None


# ---------------------------------------------------------------------------
# Pattern store
# ---------------------------------------------------------------------------

class ProactivePatternStore:
    """SQLite store of observed patterns used by the scenarios.

    Row shape: (id, pattern_type, key, value_json, count, first_seen, last_seen).
    pattern_type ∈ boot_sequence | boot_time | app_session | command_after
                   | dismissed.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proactive_patterns (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                key          TEXT NOT NULL,
                value_json   TEXT,
                count        INTEGER NOT NULL DEFAULT 1,
                first_seen   TEXT NOT NULL,
                last_seen    TEXT NOT NULL,
                UNIQUE(pattern_type, key)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ProactivePatternStore not open — call open() first")
        return self._conn

    def record(
        self, pattern_type: str, key: str, value: object | None = None,
    ) -> int:
        """Upsert a pattern observation; increments count. Returns new count."""
        conn = self._ensure()
        now = datetime.now(tz=timezone.utc).isoformat()
        value_json = json.dumps(value) if value is not None else None
        row = conn.execute(
            "SELECT count FROM proactive_patterns "
            "WHERE pattern_type = ? AND key = ?",
            (pattern_type, key),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO proactive_patterns "
                "(pattern_type, key, value_json, count, first_seen, last_seen) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (pattern_type, key, value_json, now, now),
            )
            conn.commit()
            return 1
        new_count = row[0] + 1
        conn.execute(
            "UPDATE proactive_patterns "
            "SET count = ?, last_seen = ?, value_json = COALESCE(?, value_json) "
            "WHERE pattern_type = ? AND key = ?",
            (new_count, now, value_json, pattern_type, key),
        )
        conn.commit()
        return new_count

    def get(self, pattern_type: str, key: str) -> dict | None:
        conn = self._ensure()
        row = conn.execute(
            "SELECT pattern_type, key, value_json, count, first_seen, last_seen "
            "FROM proactive_patterns WHERE pattern_type = ? AND key = ?",
            (pattern_type, key),
        ).fetchone()
        return self._row(row) if row else None

    def list_type(self, pattern_type: str) -> list[dict]:
        conn = self._ensure()
        rows = conn.execute(
            "SELECT pattern_type, key, value_json, count, first_seen, last_seen "
            "FROM proactive_patterns WHERE pattern_type = ? "
            "ORDER BY count DESC",
            (pattern_type,),
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: tuple) -> dict:
        return {
            "pattern_type": row[0],
            "key": row[1],
            "value": json.loads(row[2]) if row[2] else None,
            "count": row[3],
            "first_seen": row[4],
            "last_seen": row[5],
        }


# ---------------------------------------------------------------------------
# Similarity helper (boot routine — Layer 2 uses it)
# ---------------------------------------------------------------------------

def jaccard(a: set, b: set) -> float:
    """Jaccard index of two sets; 0.0 when both empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ProactiveEngine:
    """Foundation of the proactive engine: level management + dismiss logic.

    Layer 2 adds the scenario evaluators and the background loop; this
    class already owns the level state, the adaptive downgrade, and the
    "may I speak this importance tier?" gate the scenarios will consult.
    """

    def __init__(
        self,
        *,
        config,
        store: ProactivePatternStore,
        bus=None,
        usage_stats=None,
        on_level_change=None,
    ) -> None:
        self._config = config
        self._store = store
        self._bus = bus
        self._usage = usage_stats
        self._on_level_change = on_level_change
        self._level = InitiativeLevel.from_str(
            getattr(config, "initiative_level", "high")
        )
        # Consecutive dismissals this session (reset on any accepted/explicit
        # level change).
        self._session_dismiss_streak = 0
        # Session counters for status reporting.
        self.suggested_count = 0
        self.accepted_count = 0
        self.dismissed_count = 0

    # -- level state -----------------------------------------------------

    @property
    def level(self) -> InitiativeLevel:
        return self._level

    def set_level(self, level: InitiativeLevel) -> None:
        """Explicitly set the level (voice command). Persists + resets streak."""
        self._level = level
        self._session_dismiss_streak = 0
        self._persist_level()
        log.info("[proactive] initiative level set to %s", level.value)

    def raise_level(self) -> InitiativeLevel:
        """"Be more active" — one step louder."""
        self.set_level(self._level.upgraded())
        return self._level

    def lower_level(self) -> InitiativeLevel:
        """"Don't bother me" — one step quieter."""
        self.set_level(self._level.downgraded())
        return self._level

    def _persist_level(self) -> None:
        try:
            self._config.initiative_level = self._level.value
            if self._on_level_change is not None:
                self._on_level_change()
        except Exception:
            log.warning("[proactive] could not persist level", exc_info=True)

    # -- dismissal -------------------------------------------------------

    def record_dismiss(self, suggestion_key: str) -> None:
        """Register a dismissal. Three in a row auto-downgrades the level.

        The per-suggestion counter in the pattern store lets scenarios
        back off an individual nudge; the session streak drives the global
        downgrade.
        """
        self.dismissed_count += 1
        self._session_dismiss_streak += 1
        try:
            self._store.record("dismissed", suggestion_key)
        except Exception:
            log.warning("[proactive] could not record dismissal", exc_info=True)

        threshold = getattr(self._config, "dismiss_downgrade_count", 3)
        if self._session_dismiss_streak >= threshold:
            if self._level is not InitiativeLevel.SILENT:
                old = self._level
                self._level = self._level.downgraded()
                self._persist_level()
                log.info(
                    "[proactive] auto-downgraded %s -> %s after %d dismissals",
                    old.value, self._level.value, self._session_dismiss_streak,
                )
            self._session_dismiss_streak = 0

    def record_accept(self) -> None:
        """A suggestion was accepted — resets the dismissal streak."""
        self.accepted_count += 1
        self._session_dismiss_streak = 0

    def dismiss_count_for(self, suggestion_key: str) -> int:
        row = self._store.get("dismissed", suggestion_key)
        return row["count"] if row else 0

    # -- speak gate ------------------------------------------------------

    def may_speak(self, importance: str) -> bool:
        """True if the current level permits an *importance*-tier suggestion.

        importance ∈ normal | important | critical.
        """
        if not getattr(self._config, "enabled", True):
            return False
        return importance in _LEVEL_ALLOWS.get(self._level, set())

    def speak(self, text: str, *, importance: str = "normal") -> bool:
        """Publish a ProactiveSpeech if the level allows this importance.

        Returns True if spoken. Increments the session suggestion counter.
        """
        if not self.may_speak(importance):
            return False
        if self._bus is None:
            log.debug("[proactive] no bus wired; would say: %s", text)
            return False
        from jarvis.core.events import ProactiveSpeech

        self._bus.publish(ProactiveSpeech(text=text))
        self.suggested_count += 1
        return True

    def status(self) -> dict:
        """Snapshot for the proactive_status voice command."""
        return {
            "level": self._level.value,
            "enabled": bool(getattr(self._config, "enabled", True)),
            "suggested": self.suggested_count,
            "accepted": self.accepted_count,
            "dismissed": self.dismissed_count,
        }
