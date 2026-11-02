"""Memory search must work when fact text and tags are DPAPI-encrypted.

The root conftest turns DPAPI off for the whole suite, so every other memory
test runs on plaintext. On Windows, though, encryption is always on, and the
FTS index / LIKE fallback / tag filters then compare a plaintext query with
ciphertext and find nothing.

Every test here runs twice: ``plain`` (as the rest of the suite) and
``dpapi`` (encryption on). Both modes must behave the same.

The fake DPAPI is NON-deterministic on purpose, like the real one: the same
text encrypted twice gives different ciphertext. The XOR fake in
tests/security is deterministic, which would hide the broken tag filter.
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import pytest

from jarvis.memory.auto_recall import get_relevant_memories
from jarvis.memory.relations import RelationStore
from jarvis.memory.store import MemoryStore
from jarvis.platform.secrets import SCHEME_PREFIX

_SALT_LEN = 8


def _fake_protect(data: bytes) -> bytes:
    salt = os.urandom(_SALT_LEN)
    return salt + bytes(b ^ salt[i % _SALT_LEN] for i, b in enumerate(data))


def _fake_unprotect(blob: bytes) -> bytes:
    salt, body = blob[:_SALT_LEN], blob[_SALT_LEN:]
    return bytes(b ^ salt[i % _SALT_LEN] for i, b in enumerate(body))


@pytest.fixture(params=["plain", "dpapi"])
def mode(request):
    if request.param == "plain":
        yield "plain"  # root conftest already disables DPAPI
        return
    with (
        patch("jarvis.platform.secrets.dpapi_available", return_value=True),
        patch("jarvis.platform.secrets._protect", side_effect=_fake_protect),
        patch("jarvis.platform.secrets._unprotect", side_effect=_fake_unprotect),
    ):
        yield "dpapi"


@pytest.fixture
async def store(tmp_path, mode):
    s = MemoryStore(db_path=tmp_path / "memory.db")
    await s.open()
    yield s
    await s.close()


async def test_fake_dpapi_really_encrypts(store, mode):
    """Guard: in dpapi mode the DB must hold ciphertext, else the suite proves nothing."""
    fid = await store.add("My browser is Chrome")
    conn = sqlite3.connect(str(store.db_path))
    try:
        raw = conn.execute("SELECT text FROM facts WHERE id = ?", (fid,)).fetchone()[0]
    finally:
        conn.close()
    if mode == "dpapi":
        assert raw.startswith(SCHEME_PREFIX)
        assert "Chrome" not in raw
    else:
        assert raw == "My browser is Chrome"


async def test_search_single_word(store):
    fid = await store.add("My browser is Chrome")
    await store.add("Something unrelated")
    assert [f.id for f in await store.search("Chrome")] == [fid]


async def test_search_cyrillic_word(store):
    fid = await store.add("Пользователь любит проходить игры на 100%", language="ru")
    assert [f.id for f in await store.search("игры")] == [fid]


async def test_search_or_query_as_built_by_auto_recall(store):
    a = await store.add("My browser is Chrome")
    b = await store.add("I play Stalker every evening")
    await store.add("Something unrelated")
    found = {f.id for f in await store.search("Chrome OR Stalker")}
    assert found == {a, b}


async def test_search_implicit_and(store):
    both = await store.add("Chrome and Steam are open")
    await store.add("Only Chrome here")
    assert [f.id for f in await store.search("Chrome Steam")] == [both]


async def test_search_with_tag_filter(store):
    tagged = await store.add("Ачивка Исследователь в Stalker", tags=["stalker_ach"])
    await store.add("Другая заметка про Stalker", tags=["other"])
    found = await store.search("Stalker", tag="stalker_ach")
    assert [f.id for f in found] == [tagged]


async def test_relations_find_facts_by_tag(store):
    fid = await store.add("Ачивка Исследователь в Stalker", tags=["stalker_ach"])
    relations = RelationStore(db_path=store.db_path)
    relations.init_schema()
    assert fid in relations.find_all_related_to_tag("stalker_ach")


async def test_auto_recall_injects_matching_memory(store):
    await store.add("User's favourite game is Stalker", is_permanent=True)
    injected = await get_relevant_memories(store, "Stalker")
    assert injected is not None
    assert "favourite game is Stalker" in injected
