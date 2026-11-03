"""jarvis.memory.collections: saved checklists / recipes from web pages."""

from __future__ import annotations

import pytest

from jarvis.memory.collections import (
    CollectionStore,
    Item,
    NewItem,
    item_match_score,
    match_score,
    normalize_name,
)


@pytest.fixture
def store(tmp_path) -> CollectionStore:
    return CollectionStore(tmp_path / "collections.db")


def _guide(store: CollectionStore, url: str = "https://steam.example/guide") -> str:
    col = store.save(
        kind="checklist",
        title="Bleach: Rebirth of Souls - achievements",
        source_url=url,
        source_title="Steam :: All achievements (100%)",
        items=[
            NewItem(name="Крылья феникса", role="task", details="купить 10 видов"),
            NewItem(name="Панцирь черепахи", role="task", details="купить 10 видов"),
            NewItem(name="Король душ", role="task", details="все реальные моменты"),
        ],
    )
    return col.id


class TestSaveAndRead:
    def test_roundtrip(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        assert col.kind == "checklist"
        assert [i.name for i in col.items] == ["Крылья феникса", "Панцирь черепахи", "Король душ"]
        assert col.items[0].details == "купить 10 видов"
        assert (col.total, col.done_count) == (3, 0)

    def test_recipe_parts(self, store):
        col = store.save(
            kind="recipe", title="Борщ", servings=4,
            items=[
                NewItem(name="Свёкла", role="ingredient", quantity=0.5, unit="кг"),
                NewItem(name="Сварить бульон", role="step"),
            ],
        )
        assert [i.name for i in col.ingredients()] == ["Свёкла"]
        assert col.ingredients()[0].quantity == 0.5
        assert [i.name for i in col.steps()] == ["Сварить бульон"]
        assert col.total == 0  # a recipe has nothing to tick off
        assert col.servings == 4

    def test_text_columns_go_through_encryption(self, store, monkeypatch):
        """Same column-level protection as memory.db: every text value is
        passed to encrypt_value on write and decrypt_value on read."""
        import jarvis.memory.collections as mod

        monkeypatch.setattr(mod, "encrypt_value", lambda v: f"enc:{v}" if v else v)
        monkeypatch.setattr(
            mod, "decrypt_value", lambda v: v[4:] if v and v.startswith("enc:") else v
        )
        cid = _guide(store)
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            raw = conn.execute(
                "SELECT name FROM collection_items WHERE collection_id = ?", (cid,)
            ).fetchone()[0]
            title = conn.execute("SELECT title FROM collections").fetchone()[0]
        assert raw.startswith("enc:")
        assert title.startswith("enc:")
        col = store.get(cid)
        assert col is not None
        assert col.items[0].name == "Крылья феникса"


class TestTicks:
    def test_set_done_and_progress(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        assert store.set_done(cid, [col.items[0].id]) == 1
        col = store.get(cid)
        assert col is not None
        assert (col.done_count, col.total) == (1, 3)
        assert [i.name for i in col.remaining()] == ["Панцирь черепахи", "Король душ"]

    def test_set_done_twice_changes_nothing(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        store.set_done(cid, [col.items[0].id])
        assert store.set_done(cid, [col.items[0].id]) == 0

    def test_untick(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        store.set_done(cid, [col.items[0].id])
        store.set_done(cid, [col.items[0].id], done=False)
        col = store.get(cid)
        assert col is not None
        assert col.done_count == 0

    def test_resave_same_page_keeps_ticks(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        store.set_done(cid, [col.items[2].id])  # Король душ
        again = store.save(
            kind="checklist", title="Bleach", source_url="https://steam.example/guide",
            items=[
                NewItem(name="король душ", role="task"),
                NewItem(name="Новое достижение", role="task"),
            ],
        )
        assert again.id == cid
        assert len(store.list_all()) == 1
        assert {i.name: i.done for i in again.items} == {
            "король душ": True, "Новое достижение": False,
        }

    def test_delete(self, store):
        cid = _guide(store)
        assert store.delete(cid)
        assert store.get(cid) is None
        assert store.list_all() == []


class TestFind:
    def test_empty_query_is_most_recent(self, store):
        _guide(store)
        store.save(kind="recipe", title="Борщ", items=[NewItem(name="Свёкла", role="ingredient")])
        found = store.find("")
        assert found is not None
        assert found.title == "Борщ"

    def test_kind_filter(self, store):
        _guide(store)
        store.save(kind="recipe", title="Борщ", items=[NewItem(name="Свёкла", role="ingredient")])
        found = store.find("", kinds=("checklist",))
        assert found is not None
        assert found.kind == "checklist"

    def test_spoken_cyrillic_finds_latin_title(self, store):
        _guide(store)
        found = store.find("блич")
        assert found is not None
        assert found.title.startswith("Bleach")

    def test_declined_name_finds_recipe(self, store):
        store.save(kind="recipe", title="Борщ", items=[NewItem(name="Свёкла", role="ingredient")])
        assert store.find("борща") is not None

    def test_unrelated_name_finds_nothing(self, store):
        _guide(store)
        assert store.find("элден ринг") is None

    def test_match_items_ranks_exact_first(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        ranked = store.match_items(col, "крылья феникса")
        assert ranked[0][1].name == "Крылья феникса"
        assert ranked[0][0] == 1.0

    def test_match_items_finds_a_row_by_its_details(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        ranked = store.match_items(col, "реальные моменты")
        assert ranked[0][1].name == "Король душ"


@pytest.mark.parametrize(
    ("query", "name", "details", "low", "high"),
    [
        ("Пройти обучение", "Пройти обучение", "", 1.0, 1.0),
        ("феникс", "Крылья феникса", "", 0.7, 0.9),
        # One shared verb out of four words is not a match.
        ("пройти первую главу в сюжете", "Пройти обучение", "", 0.0, 0.54),
        ("пройти первую главу в сюжете", "Июльский Дождь", "Пройти первую главу в сюжете",
         0.85, 0.85),
        # Same word, different ending.
        ("первой главы", "Июльский Дождь", "Пройти первую главу", 0.85, 0.85),
    ],
)
def test_item_match_score(query, name, details, low, high):
    item = Item(id=1, position=0, role="task", section="", name=name, details=details,
                quantity=None, unit="", done=False)
    assert low <= item_match_score(query, item) <= high


@pytest.mark.parametrize(
    ("query", "candidate", "low", "high"),
    [
        ("Крылья феникса", "крылья феникса", 1.0, 1.0),
        ("панцирь", "Панцирь черепахи", 0.9, 1.0),
        ("блич", "Bleach: Rebirth of Souls", 0.6, 1.0),
        ("пицца", "Борщ", 0.0, 0.4),
        ("", "Борщ", 0.0, 0.0),
    ],
)
def test_match_score(query, candidate, low, high):
    assert low <= match_score(query, candidate) <= high


def test_normalize_name():
    assert normalize_name("  Ёлка — «Зелёная»!  ") == "елка зеленая"


class TestManyLists:
    """Choosing among dozens of saved pages."""

    def test_list_all_gives_headers_with_counts_but_no_items(self, store):
        cid = _guide(store)
        col = store.get(cid)
        assert col is not None
        store.set_done(cid, [col.items[0].id])
        header = store.list_all()[0]
        assert header.items == ()
        assert (header.task_done, header.task_total) == (1, 3)
        assert (header.done_count, header.total) == (1, 3)

    def test_touch_makes_a_list_the_latest(self, store):
        first = _guide(store, url="https://a.example/1")
        store.save(kind="checklist", title="Elden Ring", items=[NewItem(name="A", role="task")])
        assert store.list_all()[0].title == "Elden Ring"
        store.touch(first)
        assert store.list_all()[0].id == first

    def test_alias_is_found_and_wins(self, store):
        cid = _guide(store)
        store.save(kind="checklist", title="Блич фанфик", items=[NewItem(name="A", role="task")])
        store.set_alias(cid, "мой блич")
        found = store.find("мой блич")
        assert found is not None
        assert found.id == cid
        assert found.label == "мой блич"

    def test_alias_belongs_to_one_list(self, store):
        a = _guide(store, url="https://a.example/1")
        b = _guide(store, url="https://a.example/2")
        store.set_alias(a, "блич")
        store.set_alias(b, "Блич")
        assert store.get(a).alias == ""  # type: ignore[union-attr]
        assert store.get(b).alias == "Блич"  # type: ignore[union-attr]

    def test_alias_survives_resaving_the_page(self, store):
        cid = _guide(store)
        store.set_alias(cid, "блич")
        _guide(store)
        assert store.get(cid).alias == "блич"  # type: ignore[union-attr]

    def test_find_by_url_ignores_fragment_and_slash(self, store):
        cid = _guide(store, url="https://steam.example/guide?id=1")
        found = store.find_by_url("https://steam.example/guide?id=1/#section")
        assert found is not None and found.id == cid
        assert store.find_by_url("https://other.example/") is None
        assert store.find_by_url("") is None

    def test_candidates_ranked(self, store):
        _guide(store, url="https://a.example/1")
        store.save(kind="checklist", title="Elden Ring", items=[NewItem(name="A", role="task")])
        ranked = store.candidates("элден ринг")
        assert [c.title for _s, c in ranked] == ["Elden Ring"]


def test_old_database_is_migrated(tmp_path):
    """A collections.db created before the alias column existed opens fine
    and gains the column (users already have one from the first version)."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE collections (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '', source_title TEXT NOT NULL DEFAULT '',
                servings REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO collections VALUES ('c1', 'checklist', 'Old guide', 'u', 't', NULL,
                '2026-11-01', '2026-11-01');
        """)
    store = CollectionStore(path)
    col = store.get("c1")
    assert col is not None
    assert (col.title, col.alias) == ("Old guide", "")
    store.set_alias("c1", "старый")
    assert store.get("c1").alias == "старый"  # type: ignore[union-attr]
