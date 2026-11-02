"""import_starter writes my_preferences.yaml into the store Jarvis reads (memory.db)."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.memory.preferences_file import ensure_preferences_file
from jarvis.memory.store import MemoryStore
from jarvis.memory.user_profile import UserProfile
from jarvis.personal_starter.import_starter import (
    IMPORT_TAG,
    MARKER_FILENAME,
    PREFERENCE_TAG,
    import_starter,
)

_YAML = """\
preferences:
  time_format_ru: "24h"
facts:
  - content: "Пользователь любит проходить игры на 100%"
    category: "personal/interests"
    tags: ["games"]
    is_permanent: true
  - "Любимый браузер — Chrome"
projects:
  - name: "JARVIS"
    description: "локальный ассистент"
work_schedule:
  type: "shift"
  rotation: "monthly"
  shifts:
    day:
      days: ["wed", "thu"]
      hours: "10.5h"
profile:
  name: "Тест"
  city: "Тестоград"
"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    yaml_path = tmp_path / "my_preferences.yaml"
    yaml_path.write_text(_YAML, encoding="utf-8")
    return yaml_path, tmp_path / "memory"


def _facts(memory_dir):
    async def run():
        store = MemoryStore(db_path=memory_dir / "memory.db")
        await store.open()
        try:
            return await store.count(), await store.search("Chrome")
        finally:
            await store.close()

    return asyncio.run(run())


def test_import_writes_into_memory_db(setup):
    yaml_path, memory_dir = setup
    assert import_starter(yaml_path, memory_dir) is True

    count, found = _facts(memory_dir)
    # 1 preference + 2 facts + 1 project + 1 schedule
    assert count == 5
    assert any("Chrome" in fact.text for fact in found)
    assert (memory_dir / MARKER_FILENAME).exists()


def test_profile_goes_to_user_profile(setup):
    yaml_path, memory_dir = setup
    import_starter(yaml_path, memory_dir)
    profile = UserProfile(path=memory_dir / "user_profile.json")
    assert profile.get("name") == "Тест"
    assert profile.get("city") == "Тестоград"


def test_preferences_carry_preference_and_import_tags(setup):
    yaml_path, memory_dir = setup
    import_starter(yaml_path, memory_dir)

    async def run():
        store = MemoryStore(db_path=memory_dir / "memory.db")
        await store.open()
        try:
            return await store.search("Налаштування")
        finally:
            await store.close()

    facts = asyncio.run(run())
    assert facts, "preference fact not found"
    tags = set(facts[0].tags)
    assert {PREFERENCE_TAG, IMPORT_TAG} <= tags


def test_second_run_is_skipped_unless_forced(setup):
    yaml_path, memory_dir = setup
    import_starter(yaml_path, memory_dir)
    assert import_starter(yaml_path, memory_dir) is True
    assert _facts(memory_dir)[0] == 5
    assert import_starter(yaml_path, memory_dir, force=True) is True
    assert _facts(memory_dir)[0] == 10


def test_empty_template_imports_nothing(tmp_path):
    yaml_path = tmp_path / "my_preferences.yaml"
    ensure_preferences_file(yaml_path)
    memory_dir = tmp_path / "memory"
    assert import_starter(yaml_path, memory_dir) is False
    assert not (memory_dir / "memory.db").exists()
    assert not (memory_dir / MARKER_FILENAME).exists()


def test_broken_yaml_reports_instead_of_crashing(tmp_path):
    yaml_path = tmp_path / "my_preferences.yaml"
    yaml_path.write_text("facts: [unclosed\n", encoding="utf-8")
    assert import_starter(yaml_path, tmp_path / "memory") is False
