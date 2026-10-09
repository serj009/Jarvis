"""ShardManager: category-based database sharding with auto-creation.

Instead of one monolithic memory.db, facts are distributed across
per-category SQLite databases:

    %APPDATA%/Jarvis/memory/shards/
        general.db
        games.db
        personal.db
        tech.db
        work.db
        ...

DynamicShardManager extends this with automatic shard creation:
when a category accumulates >= SHARD_THRESHOLD facts in the default
shard, it gets promoted to its own .db file.

Topic normalization prevents fragmentation: "diablo", "cuphead",
"baldurs gate" all map to the "games" shard via normalize_topic().
Works across languages (капхед/балдурсгейт -> games).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Minimum facts in a category before it gets its own shard.
SHARD_THRESHOLD = 10

# ---------------------------------------------------------------------------
# Topic normalization — prevents shard fragmentation
# ---------------------------------------------------------------------------

# Map of known topics -> canonical shard name.
# Patterns are checked case-insensitively against fact text and category.
_TOPIC_RULES: dict[str, list[str]] = {
    "games": [
        # Game titles and gaming terms (EN/RU/UK)
        r"game|steam|xbox|playstation|nintendo|rpg|fps|mmorpg",
        r"diablo|stalker|cyberpunk|skyrim|witcher|elden.ring|baldur",
        r"minecraft|gta|dark.souls|cuphead|hollow.knight",
        r"игр|стим|геймпад|ачивк|achievement|save.?game|сохранен",
        r"капхед|скайрим|ведьмак|балдур|кіберпанк|відьмак",
    ],
    "tech": [
        r"python|javascript|code|program|api|github|docker|linux",
        r"gpu|cpu|ram|vram|nvidia|amd|intel|ssd|hardware",
        r"ollama|whisper|piper|model|neural|ai\b|ml\b",
        r"програм|код|сервер|база.данн|алгоритм|бібліотек",
    ],
    "personal": [
        r"my name|my age|my birthday|born|family|wife|husband|friend",
        r"favorite|prefer|like|love|hate|hobby|hobbi",
        r"меня зовут|мой день|родил|семья|жена|муж|друг|хобби",
        r"мене звати|мій день|сім.я|дружина|чоловік|друг|хобі",
    ],
    "work": [
        r"work|job|office|meeting|deadline|project|team|manager",
        r"salary|schedule|shift|colleague|boss|task|sprint",
        r"работ|офис|собрание|проект|коллег|задач|смена|зарплат",
        r"робот|офіс|зустріч|проект|колег|задач|зміна|зарплат",
    ],
    "media": [
        r"movie|film|series|anime|manga|ranobe|book|music|song|album",
        r"youtube|netflix|spotify|twitch|podcast",
        r"фильм|сериал|аниме|манга|ранобе|книг|музык|песн",
        r"фільм|серіал|анімe|манга|книг|музик|пісн",
    ],
}

# Compiled patterns per shard
_COMPILED_RULES: dict[str, list[re.Pattern]] = {}


def _ensure_compiled() -> None:
    if _COMPILED_RULES:
        return
    for shard, patterns in _TOPIC_RULES.items():
        _COMPILED_RULES[shard] = [
            re.compile(p, re.IGNORECASE) for p in patterns
        ]


def normalize_topic(text: str, category: str = "general") -> str:
    """Map text + category to a canonical shard name.

    Returns the best-matching shard name, or the category as-is
    if no rule matches.
    """
    _ensure_compiled()

    # If category already matches a known shard, use it
    if category in _TOPIC_RULES:
        return category

    # Score each shard by number of pattern matches
    combined = f"{category} {text}"
    best_shard = category
    best_score = 0

    for shard, patterns in _COMPILED_RULES.items():
        score = sum(1 for p in patterns if p.search(combined))
        if score > best_score:
            best_score = score
            best_shard = shard

    return best_shard if best_score > 0 else category


# ---------------------------------------------------------------------------
# ShardManager
# ---------------------------------------------------------------------------

def _shard_schema() -> str:
    """Same schema as store.py but without FTS (kept per-shard for simplicity)."""
    return """
    CREATE TABLE IF NOT EXISTS facts (
        id              TEXT PRIMARY KEY,
        text            TEXT NOT NULL,
        category        TEXT NOT NULL DEFAULT 'general',
        source          TEXT NOT NULL DEFAULT 'user',
        language        TEXT NOT NULL DEFAULT 'en',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        access_count    INTEGER NOT NULL DEFAULT 0,
        last_accessed_at TEXT,
        is_permanent    INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS fact_tags (
        fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
        tag     TEXT NOT NULL,
        PRIMARY KEY (fact_id, tag)
    );
    CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
    CREATE INDEX IF NOT EXISTS idx_fact_tags_tag ON fact_tags(tag);
    """


class ShardManager:
    """Manages per-category SQLite shard files.

    Usage:
        shards = ShardManager(root=Path(".../memory/shards"))
        shards.ensure_shard("games")
        conn = shards.connect("games")
        ...
        conn.close()
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def shard_path(self, shard_name: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", shard_name.lower())
        return self._root / f"{safe}.db"

    def ensure_shard(self, shard_name: str) -> Path:
        """Create shard database if it doesn't exist. Returns path."""
        path = self.shard_path(shard_name)
        if not path.exists():
            conn = sqlite3.connect(str(path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_shard_schema())
            conn.close()
            log.info("created shard: %s", path.name)
        return path

    def connect(self, shard_name: str) -> sqlite3.Connection:
        """Get a connection to a shard (creates if needed)."""
        self.ensure_shard(shard_name)
        conn = sqlite3.connect(str(self.shard_path(shard_name)))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def list_shards(self) -> list[str]:
        """List all existing shard names."""
        return [
            p.stem for p in self._root.glob("*.db")
        ]

    def shard_stats(self) -> dict[str, int]:
        """Return {shard_name: fact_count} for all shards."""
        stats = {}
        for name in self.list_shards():
            try:
                conn = self.connect(name)
                row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
                stats[name] = row[0] if row else 0
                conn.close()
            except Exception:
                stats[name] = -1
        return stats
