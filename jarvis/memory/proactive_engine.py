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

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


def _default_db_path() -> Path:
    # Resolved at call time via the single data-dir resolver (jarvis.paths);
    # layout unchanged: <data_dir>/proactive_patterns.db.
    from jarvis.paths import data_path

    return data_path("proactive_patterns.db")


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
    def from_str(cls, value: str) -> InitiativeLevel:
        try:
            return cls(value.strip().lower())
        except (ValueError, AttributeError):
            return cls.HIGH

    def downgraded(self) -> InitiativeLevel:
        """Return the next quieter level (SILENT is the floor)."""
        order = [self.HIGH, self.MEDIUM, self.LOW, self.SILENT]
        idx = order.index(self)
        return order[min(idx + 1, len(order) - 1)]

    def upgraded(self) -> InitiativeLevel:
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
        from jarvis import platform as platform_ops

        return platform_ops.foreground_window_title()
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
        self._db_path = db_path if db_path is not None else _default_db_path()
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
        now = datetime.now(tz=UTC).isoformat()
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
        # Layer 2 runtime state.
        self._fired_at: dict[str, datetime] = {}
        self._focus_title: str = ""
        self._focus_since: datetime | None = None
        self._running = False

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

    # -- cooldowns / dismissal timing (Layer 2) --------------------------

    def last_dismiss_time(self, suggestion_key: str) -> datetime | None:
        """When this suggestion was last dismissed (for per-item snooze)."""
        row = self._store.get("dismissed", suggestion_key)
        if not row or not row.get("last_seen"):
            return None
        try:
            return datetime.fromisoformat(row["last_seen"])
        except ValueError:
            return None

    def cooldown_ok(self, key: str, seconds: float, now: datetime) -> bool:
        """True if *key* has not fired within *seconds*. Records the fire time."""
        last = self._fired_at.get(key)
        if last is not None and (now - last).total_seconds() < seconds:
            return False
        self._fired_at[key] = now
        return True

    # -- foreground / boot tracking (Layer 2) ----------------------------

    def observe_foreground(self, title: str, now: datetime) -> float:
        """Update the focus timer for the current foreground window.

        Returns seconds the *current* window has held focus continuously.
        A change of window resets the timer.
        """
        if title != self._focus_title:
            self._focus_title = title
            self._focus_since = now
            return 0.0
        if self._focus_since is None:
            self._focus_since = now
            return 0.0
        return (now - self._focus_since).total_seconds()

    def leisure_seconds(self, title: str, now: datetime) -> float:
        """Seconds the current leisure window has held focus (0 if not leisure)."""
        secs = self.observe_foreground(title, now)
        return secs if classify_leisure(title) is not None else 0.0

    def record_boot_sequence(self, apps: list[str], now: datetime) -> None:
        """Record a boot sequence keyed by hour+weekday context."""
        if not apps:
            return
        hour = now.astimezone().hour
        weekday = now.astimezone().weekday()
        slot = "weekday" if weekday < 5 else "weekend"
        key = f"{hour:02d}:{slot}"
        context = [f"h{hour}", slot]
        self._store.record(
            "boot_sequence", key, value={"apps": apps, "context": context},
        )

    def record_command_after(self, previous: str, following: str) -> None:
        """Record that *following* happened after *previous*."""
        if not previous or not following:
            return
        self._store.record("command_after", f"{previous}||{following}")

    # -- scenario dispatch + background loop -----------------------------

    def maybe_speak_scenario(
        self, result: tuple[str, str, str] | None, *, pause_media: bool = False,
    ) -> bool:
        """Given a scenario result, apply the level gate and speak.

        Returns True if a suggestion was spoken.
        """
        if result is None:
            return False
        text, importance, _key = result
        if not self.may_speak(importance):
            return False
        if pause_media and importance in ("important", "critical"):
            media_pause()
        return self.speak(text, importance=importance)

    async def run(self, tick_fn) -> None:
        """Background loop. *tick_fn* is an async/sync callable doing one
        evaluation pass; kept injectable so the loop itself has no policy.
        """
        interval = getattr(self._config, "check_interval_seconds", 60)
        self._running = True
        log.info("[proactive] engine loop started (interval=%ss)", interval)
        try:
            while self._running:
                try:
                    res = tick_fn()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    log.exception("[proactive] tick error")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("[proactive] engine loop cancelled")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Layer 2: media pause + scenario evaluation + background loop
# ---------------------------------------------------------------------------

def media_pause() -> bool:
    """Best-effort media pause (play/pause toggle). True if a key was sent.

    Reuses the media_control key sender so an important nudge (project,
    health) can lower the noise before Jarvis speaks.
    """
    try:
        from jarvis.tools.local.media_control import _press_media_key

        result = _press_media_key("play_pause")
        return bool(result.get("success"))
    except Exception:
        log.debug("[proactive] media pause failed", exc_info=True)
        return False


# Scenario cool-downs (seconds) — a fired scenario won't repeat sooner.
_HEALTH_COOLDOWN = 2 * 3600
_PROJECT_DISMISS_COOLDOWN = 24 * 3600


class ProactiveScenarios:
    """The five scenario evaluators, split out for focused testing.

    Each returns an optional (text, importance, suggestion_key) tuple when
    it wants to speak, or None. The engine applies the level gate and
    speaks. Every evaluator takes explicit ``now`` / ``foreground`` /
    ``focus_seconds`` so tests can drive them deterministically.
    """

    def __init__(self, engine: ProactiveEngine) -> None:
        self._e = engine

    # -- A. abandoned project --------------------------------------------

    def abandoned_project(
        self, *, foreground: str, leisure_seconds: float, now: datetime,
        stale_project: dict | None,
    ) -> tuple[str, str, str] | None:
        """Leisure app for 2+ h AND a project untouched for 3+ days."""
        kind = classify_leisure(foreground)
        if kind is None:
            return None
        threshold = getattr(self._e._config, "leisure_hours_threshold", 2.0) * 3600
        if leisure_seconds < threshold:
            return None
        if not stale_project:
            return None
        name = stale_project.get("name", "a project")
        days = stale_project.get("days_idle", 0)
        key = f"project:{name}"
        # Respect a 24 h snooze after a dismissal of this project.
        last = self._e.last_dismiss_time(key)
        if last is not None and (now - last).total_seconds() < _PROJECT_DISMISS_COOLDOWN:
            return None
        text = (
            f"Sir, we have a project '{name}' started {days} days ago. "
            "Shall we get back to it?"
        )
        return (text, "important", key)

    # -- B. upcoming important date --------------------------------------

    def upcoming_date(
        self, *, events: list[dict], now: datetime,
    ) -> tuple[str, str, str] | None:
        """A birthday/event fact within 7 days (checked at boot)."""
        soonest: dict | None = None
        soonest_days = 999
        for ev in events:
            days = ev.get("days_until")
            if days is None or days < 0 or days > 7:
                continue
            if days < soonest_days:
                soonest_days, soonest = days, ev
        if soonest is None:
            return None
        who = soonest.get("name", "someone")
        date = soonest.get("date", "")
        key = f"event:{who}:{date}"
        importance = "critical" if soonest_days == 0 else "important"
        when = "today" if soonest_days == 0 else f"in {soonest_days} days"
        text = (
            f"{when.capitalize()}{(', ' + date) if date else ''}, it's "
            f"{who}'s birthday. Would you like to pick a gift?"
        )
        return (text, importance, key)

    # -- C. long work session (health) -----------------------------------

    def long_work(
        self, *, foreground: str, focus_seconds: float, now: datetime,
    ) -> tuple[str, str, str] | None:
        """Same productivity app in focus for work_hours_threshold+ hours."""
        if classify_leisure(foreground) is not None:
            return None  # leisure isn't "work"
        if not foreground:
            return None
        threshold = getattr(self._e._config, "work_hours_threshold", 4.0) * 3600
        if focus_seconds < threshold:
            return None
        hours = int(focus_seconds // 3600)
        key = "health:long_work"
        if not self._e.cooldown_ok(key, _HEALTH_COOLDOWN, now):
            return None
        text = (
            f"Sir, you've been working for {hours} hours straight. "
            "Perhaps a short break?"
        )
        return (text, "critical", key)

    # -- D. boot routine --------------------------------------------------

    def boot_routine(
        self, *, current_context: set[str],
    ) -> tuple[str, str, str] | None:
        """A stored boot sequence whose context matches now by Jaccard≥thr."""
        thr = getattr(self._e._config, "boot_similarity_threshold", 0.7)
        best: dict | None = None
        best_sim = 0.0
        for row in self._e._store.list_type("boot_sequence"):
            if row["count"] < 3:
                continue
            value = row["value"] or {}
            ctx = set(value.get("context", []))
            sim = jaccard(current_context, ctx)
            if sim >= thr and sim > best_sim:
                best_sim, best = sim, row
        if best is None:
            return None
        apps = (best["value"] or {}).get("apps", [])
        if not apps:
            return None
        key = f"boot_routine:{best['key']}"
        listed = ", ".join(apps[:3])
        text = f"Shall I start your usual routine, sir? {listed}."
        return (text, "normal", key)

    # -- E. command sequence ---------------------------------------------

    def command_sequence(
        self, *, last_command: str,
    ) -> tuple[str, str, str] | None:
        """After command X, user did Y ≥5 times with ≥70% confidence."""
        rows = [
            r for r in self._e._store.list_type("command_after")
            if r["key"].startswith(f"{last_command}||")
        ]
        if not rows:
            return None
        total = sum(r["count"] for r in rows)
        best = max(rows, key=lambda r: r["count"])
        if best["count"] < 5:
            return None
        confidence = best["count"] / total if total else 0.0
        if confidence < 0.7:
            return None
        follow = best["key"].split("||", 1)[1]
        key = f"seq:{last_command}->{follow}"
        text = f"After {last_command} you usually {follow}. Shall I do it?"
        return (text, "normal", key)
