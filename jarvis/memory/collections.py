"""Saved collections: structured lists taken from web pages.

A *collection* is one saved page turned into rows the user can work with
later: the achievements of a game guide (a checklist with done / not done),
or a recipe (ingredients with quantities, and the steps).

Why a separate store and not facts in MemoryStore
-------------------------------------------------
Facts are free text for recall ("what do you know about X"). A checklist
needs state that changes ("mark 'Phoenix wings' done") and exact queries
("what is left", "how many percent") over a known set of rows. Fitting
that into facts would mean rewriting fact text on every tick and asking
the LLM to count, which an 8B model does unreliably. So the structure
lives here, and save_page_knowledge also writes one short summary fact to
MemoryStore, so auto-recall still knows the collection exists.

Storage: ``<data dir>/memory/collections.db`` (SQLite). Text columns go
through the same column-level DPAPI encryption as memory.db
(``jarvis.platform.encrypted_db``), so the content is protected the same
way; ids, counters, flags and timestamps stay plain. Because names are
encrypted, matching ("блич" -> the Bleach guide) happens in Python after
decrypting, which is cheap at the size of a single collection.

All methods are synchronous and short; call them via asyncio.to_thread
from async code.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from jarvis.platform.encrypted_db import decrypt_value, encrypt_value

log = logging.getLogger(__name__)

CollectionKind = Literal["checklist", "recipe", "list"]
ItemRole = Literal["task", "ingredient", "step", "item"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    source_url  TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    alias       TEXT NOT NULL DEFAULT '',
    servings    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    role          TEXT NOT NULL,
    section       TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    details       TEXT NOT NULL DEFAULT '',
    quantity      REAL,
    unit          TEXT NOT NULL DEFAULT '',
    done          INTEGER NOT NULL DEFAULT 0,
    done_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_collection
    ON collection_items(collection_id, position);
"""


@dataclass(frozen=True)
class NewItem:
    """An item as extracted from a page, before it is stored."""

    name: str
    role: ItemRole = "item"
    section: str = ""
    details: str = ""
    quantity: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class Item:
    id: int
    position: int
    role: str
    section: str
    name: str
    details: str
    quantity: float | None
    unit: str
    done: bool


@dataclass(frozen=True)
class Collection:
    id: str
    kind: str
    title: str
    source_url: str
    source_title: str
    servings: float | None
    created_at: str
    updated_at: str
    items: tuple[Item, ...] = field(default=())
    # The user's own name for the list ("назови этот список блич").
    alias: str = ""
    # Tickable rows and how many are done, counted in SQL so that a header
    # loaded without its items (list_all) still knows its progress.
    task_total: int = 0
    task_done: int = 0

    @property
    def label(self) -> str:
        """How Jarvis names the list when speaking: the user's alias if set."""
        return self.alias or self.title

    def tasks(self) -> list[Item]:
        """Rows that can be ticked off: tasks for a checklist, every item
        for a plain list. Recipe steps and ingredients are not 'progress'."""
        if self.kind == "recipe":
            return []
        return [i for i in self.items if i.role in ("task", "item")]

    @property
    def total(self) -> int:
        return 0 if self.kind == "recipe" else self.task_total

    @property
    def done_count(self) -> int:
        return 0 if self.kind == "recipe" else self.task_done

    def remaining(self) -> list[Item]:
        return [i for i in self.tasks() if not i.done]

    def ingredients(self) -> list[Item]:
        return [i for i in self.items if i.role == "ingredient"]

    def steps(self) -> list[Item]:
        return [i for i in self.items if i.role == "step"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


_WORD = re.compile(r"[\w']+", re.UNICODE)


def _url_key(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/").lower()


def normalize_name(text: str) -> str:
    """Lower-case, ё->е, words only: the form names are compared in."""
    return " ".join(_WORD.findall(text.lower().replace("ё", "е")))


_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "i", "є": "e",
    "ґ": "g",
})


def _translit(text: str) -> str:
    """Rough Cyrillic -> Latin, so a spoken "блич" can find "Bleach"."""
    return text.translate(_TRANSLIT)


def match_score(query: str, candidate: str) -> float:
    """0..1 similarity of a spoken *query* to a stored name.

    Speech recognition writes names in the spoken language's script, while
    titles are often Latin ("блич" vs. "Bleach: Rebirth of Souls"), so the
    comparison is also made on a transliterated form and the better of the
    two counts.
    """
    direct = _match_score(query, candidate)
    if direct >= 0.95:
        return direct
    return max(direct, _match_score(_translit(normalize_name(query)),
                                    _translit(normalize_name(candidate))))


def _match_score(query: str, candidate: str) -> float:
    """0..1 similarity of a spoken *query* to a stored name.

    Speech gives approximate names ("крылья феникса" vs. "Крылья феникса",
    "блич" vs. "Bleach: Rebirth of Souls — достижения"), so: exact or
    whole-word containment scores high, otherwise a character-level ratio
    on the normalised strings, plus the best per-word ratio so a single
    distinctive word can still find its row.
    """
    q, c = normalize_name(query), normalize_name(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if f" {q} " in f" {c} ":
        return 0.95
    if f" {c} " in f" {q} ":
        return 0.9
    ratio = difflib.SequenceMatcher(None, q, c).ratio()
    q_words, c_words = q.split(), c.split()
    word_best = 0.0
    for qw in q_words:
        if len(qw) < 3:
            continue
        for cw in c_words:
            word_best = max(word_best, difflib.SequenceMatcher(None, qw, cw).ratio())
    # A single strong word match counts, but less than matching the phrase.
    return max(ratio, word_best * 0.85)


# A query word "is in" a text when some word of the text is this close to it
# (Russian endings differ: "главу" / "главе" = 0.8).
_WORD_HIT = 0.75


def _same_word(a: str, b: str) -> bool:
    """Same word up to its ending: close spelling, or a shared stem that
    leaves at most two letters of the shorter word ("первой" / "первую")."""
    if difflib.SequenceMatcher(None, a, b).ratio() >= _WORD_HIT:
        return True
    stem = len(os.path.commonprefix([a, b]))
    return stem >= max(4, min(len(a), len(b)) - 2)


def _word_coverage(query: str, text: str) -> float:
    """Share of the query's words (3+ letters) that appear in *text*."""
    q_words = [w for w in normalize_name(query).split() if len(w) >= 3]
    t_words = normalize_name(text).split()
    if not q_words or not t_words:
        return 0.0
    hits = sum(1 for qw in q_words if any(_same_word(qw, tw) for tw in t_words))
    return hits / len(q_words)


def _coverage(query: str, text: str) -> float:
    return max(_word_coverage(query, text),
               _word_coverage(_translit(normalize_name(query)),
                              _translit(normalize_name(text))))


def item_match_score(query: str, item: Item) -> float:
    """0..1 similarity of a spoken *query* to a list row.

    Unlike list titles, rows share common words ("пройти", "получить"), so a
    single matching word must not pick a row: a fuzzy name match is capped by
    how many of the query's words the name holds. The row's details are
    searched too, since users often name a row by what it asks for
    ("пройти первую главу" -> the achievement whose condition says so).
    """
    by_name = match_score(query, item.name)
    if by_name < 0.9:
        by_name = min(by_name, 0.4 + 0.5 * _coverage(query, item.name))
    if not item.details:
        return by_name
    by_details = 0.85 * _coverage(query, f"{item.name} {item.details}")
    return max(by_name, by_details)


class CollectionStore:
    """SQLite store for saved collections. Thread-safe (one lock)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            from jarvis.paths import data_path

            db_path = data_path("memory", "collections.db", create=True)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after a database was first created.

        CREATE TABLE IF NOT EXISTS leaves an existing table as it was, so a
        collections.db made by an earlier version would lack them."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(collections)")}
        if "alias" not in columns:
            conn.execute("ALTER TABLE collections ADD COLUMN alias TEXT NOT NULL DEFAULT ''")
            log.info("[collections] database migrated: added collections.alias")

    @property
    def db_path(self) -> Path:
        return self._db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- write ------------------------------------------------------------

    def save(
        self,
        *,
        kind: CollectionKind,
        title: str,
        items: Sequence[NewItem],
        source_url: str = "",
        source_title: str = "",
        servings: float | None = None,
    ) -> Collection:
        """Store a collection; re-saving the same page replaces it.

        Saving a page that is already stored (same source URL) replaces its
        rows but keeps every tick: an item whose name matches a done item of
        the previous version stays done. So re-reading an updated guide does
        not lose the user's progress.
        """
        with self._lock:
            found = self._find_by_url(source_url) if source_url else None
            previous = self.get(found.id) if found else None
            done_names = (
                {normalize_name(i.name) for i in previous.items if i.done} if previous else set()
            )
            now = _now()
            cid = previous.id if previous else uuid.uuid4().hex[:12]
            created = previous.created_at if previous else now
            with self._connect() as conn:
                if previous:
                    conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (cid,))
                    conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
                conn.execute(
                    "INSERT INTO collections (id, kind, title, source_url, source_title, "
                    "alias, servings, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cid, kind, encrypt_value(title), encrypt_value(source_url) or "",
                        encrypt_value(source_title) or "",
                        encrypt_value(previous.alias) or "" if previous else "",
                        servings, created, now,
                    ),
                )
                for pos, item in enumerate(items):
                    done = normalize_name(item.name) in done_names
                    conn.execute(
                        "INSERT INTO collection_items (collection_id, position, role, "
                        "section, name, details, quantity, unit, done, done_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            cid, pos, item.role, encrypt_value(item.section) or "",
                            encrypt_value(item.name), encrypt_value(item.details) or "",
                            item.quantity, item.unit, int(done), now if done else None,
                        ),
                    )
        if previous:
            log.info("[collections] updated %s (%d items, kept %d ticks)",
                     cid, len(items), len(done_names))
        else:
            log.info("[collections] saved %s (%s, %d items)", cid, kind, len(items))
        result = self.get(cid)
        assert result is not None
        return result

    def set_done(self, collection_id: str, item_ids: Sequence[int], done: bool = True) -> int:
        """Tick (or untick) items. Returns how many rows changed."""
        if not item_ids:
            return 0
        now = _now()
        marks = ",".join("?" * len(item_ids))
        with self._lock, self._connect() as conn:
            # Only "?" placeholders are interpolated; every value is a parameter.
            cur = conn.execute(
                "UPDATE collection_items SET done = ?, done_at = ? "
                f"WHERE collection_id = ? AND id IN ({marks}) AND done != ?",
                (int(done), now if done else None, collection_id, *item_ids, int(done)),
            )
            conn.execute(
                "UPDATE collections SET updated_at = ? WHERE id = ?", (now, collection_id)
            )
            return cur.rowcount

    def touch(self, collection_id: str) -> None:
        """Mark a collection as just used, so a later request with no name
        ("что осталось") means it. Viewing counts as using."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE collections SET updated_at = ? WHERE id = ?", (_now(), collection_id)
            )

    def set_alias(self, collection_id: str, alias: str) -> None:
        """Give a collection the user's own name. A name belongs to one
        collection only: it is taken away from any other that had it."""
        alias = alias.strip()
        key = normalize_name(alias)
        with self._lock:
            others = [
                c.id for c in self.list_all()
                if c.id != collection_id and key and normalize_name(c.alias) == key
            ]
            with self._connect() as conn:
                for other in others:
                    conn.execute("UPDATE collections SET alias = '' WHERE id = ?", (other,))
                conn.execute(
                    "UPDATE collections SET alias = ?, updated_at = ? WHERE id = ?",
                    (encrypt_value(alias) or "", _now(), collection_id),
                )

    def delete(self, collection_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
            cur = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            return cur.rowcount > 0

    # -- read -------------------------------------------------------------

    _HEADER_SQL = (
        "SELECT c.id, c.kind, c.title, c.source_url, c.source_title, c.alias, c.servings, "
        "c.created_at, c.updated_at, "
        "(SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id "
        " AND i.role IN ('task', 'item')), "
        "(SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id "
        " AND i.role IN ('task', 'item') AND i.done = 1) "
        "FROM collections c"
    )

    @staticmethod
    def _header(row: Sequence) -> Collection:
        return Collection(
            id=row[0], kind=row[1], title=decrypt_value(row[2]) or "",
            source_url=decrypt_value(row[3]) or "", source_title=decrypt_value(row[4]) or "",
            alias=decrypt_value(row[5]) or "", servings=row[6], created_at=row[7],
            updated_at=row[8], task_total=row[9], task_done=row[10],
        )

    def get(self, collection_id: str) -> Collection | None:
        with self._connect() as conn:
            row = conn.execute(
                self._HEADER_SQL + " WHERE c.id = ?", (collection_id,)
            ).fetchone()
            if row is None:
                return None
            item_rows = conn.execute(
                "SELECT id, position, role, section, name, details, quantity, unit, done "
                "FROM collection_items WHERE collection_id = ? ORDER BY position",
                (collection_id,),
            ).fetchall()
        items = tuple(
            Item(
                id=r[0], position=r[1], role=r[2],
                section=decrypt_value(r[3]) or "", name=decrypt_value(r[4]) or "",
                details=decrypt_value(r[5]) or "", quantity=r[6], unit=r[7] or "",
                done=bool(r[8]),
            )
            for r in item_rows
        )
        header = self._header(row)
        return Collection(
            id=header.id, kind=header.kind, title=header.title,
            source_url=header.source_url, source_title=header.source_title,
            servings=header.servings, created_at=header.created_at,
            updated_at=header.updated_at, items=items, alias=header.alias,
            task_total=header.task_total, task_done=header.task_done,
        )

    def list_all(self) -> list[Collection]:
        """Every collection's header (no items), most recently used first.

        Choosing which list the user means only needs titles and counts;
        loading and decrypting every row of dozens of saved pages for that
        would be thousands of DPAPI calls per question. Load the chosen one
        with get()."""
        with self._connect() as conn:
            rows = conn.execute(
                self._HEADER_SQL + " ORDER BY c.updated_at DESC, c.created_at DESC"
            ).fetchall()
        return [self._header(r) for r in rows]

    def _find_by_url(self, url: str) -> Collection | None:
        for col in self.list_all():
            if col.source_url == url:
                return col
        return None

    def candidates(
        self, query: str, *, kinds: Sequence[str] | None = None, threshold: float = 0.6
    ) -> list[tuple[float, Collection]]:
        """Headers whose alias / title / page title match *query*, best first.

        The user's alias counts most: an exact alias is a sure hit."""
        scored: list[tuple[float, Collection]] = []
        for col in self.list_all():
            if kinds is not None and col.kind not in kinds:
                continue
            alias_score = match_score(query, col.alias) if col.alias else 0.0
            if col.alias and normalize_name(query) == normalize_name(col.alias):
                alias_score = 1.01  # beats a title that happens to match exactly too
            score = max(
                alias_score, match_score(query, col.title), match_score(query, col.source_title)
            )
            if score >= threshold:
                scored.append((score, col))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def find(
        self, query: str = "", *, kinds: Sequence[str] | None = None
    ) -> Collection | None:
        """The collection the user means (with its items), or None.

        Empty query: the most recently used one (of *kinds*, if given).
        Otherwise the best alias / title match above a threshold.
        """
        if not query.strip():
            headers = [c for c in self.list_all() if kinds is None or c.kind in kinds]
            return self.get(headers[0].id) if headers else None
        scored = self.candidates(query, kinds=kinds)
        return self.get(scored[0][1].id) if scored else None

    def find_by_url(self, url: str) -> Collection | None:
        """The saved collection of the page at *url* (ignoring #fragment and
        a trailing slash), with its items."""
        key = _url_key(url)
        if not key:
            return None
        for col in self.list_all():
            if _url_key(col.source_url) == key:
                return self.get(col.id)
        return None

    @staticmethod
    def match_items(
        collection: Collection, query: str, *, limit: int = 5
    ) -> list[tuple[float, Item]]:
        """Rows of *collection* ranked by how well they match *query*."""
        scored = [
            (item_match_score(query, item), item)
            for item in collection.items
            if item.role in ("task", "item", "ingredient")
        ]
        scored = [(s, i) for s, i in scored if s >= 0.55]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]
