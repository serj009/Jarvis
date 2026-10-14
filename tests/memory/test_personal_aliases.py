"""Tests for jarvis.memory.personal_aliases — learn, resolve, prune, persistence."""

from __future__ import annotations

import json

import pytest

from jarvis.memory.personal_aliases import MAX_ALIASES, PersonalAliasStore


@pytest.fixture()
def store(tmp_path) -> PersonalAliasStore:
    return PersonalAliasStore(path=tmp_path / "personal_aliases.json")


class TestPersonalAliasStore:
    def test_starts_empty(self, store) -> None:
        assert store.alias_count() == 0
        assert store.get_all() == {}

    def test_resolve_unknown_returns_none(self, store) -> None:
        assert store.resolve("браузер") is None

    def test_learn_and_resolve(self, store) -> None:
        store.learn("браузер", "chrome")
        assert store.resolve("браузер") == "chrome"

    def test_learn_case_insensitive(self, store) -> None:
        store.learn("Браузер", "Chrome")
        assert store.resolve("браузер") == "chrome"
        assert store.resolve("БРАУЗЕР") == "chrome"

    def test_learn_strips_whitespace(self, store) -> None:
        store.learn("  редактор  ", "  code  ")
        assert store.resolve("редактор") == "code"

    def test_learn_empty_alias_ignored(self, store) -> None:
        store.learn("", "chrome")
        assert store.alias_count() == 0

    def test_learn_empty_target_ignored(self, store) -> None:
        store.learn("браузер", "")
        assert store.alias_count() == 0

    def test_learn_same_target_bumps_count(self, store) -> None:
        store.learn("браузер", "chrome")
        store.learn("браузер", "chrome")
        store.learn("браузер", "chrome")
        assert store.alias_count() == 1
        # Resolve also bumps count
        data = json.loads(store.path.read_text(encoding="utf-8"))
        assert data["браузер"]["count"] == 3

    def test_learn_different_target_replaces(self, store) -> None:
        store.learn("браузер", "chrome")
        assert store.resolve("браузер") == "chrome"
        store.learn("браузер", "firefox")
        assert store.resolve("браузер") == "firefox"
        # Count resets to 1 on replacement
        data = json.loads(store.path.read_text(encoding="utf-8"))
        # count is 1 (learn) + 1 (resolve) = 2
        assert data["браузер"]["count"] == 2

    def test_resolve_increments_count(self, store) -> None:
        store.learn("браузер", "chrome")
        store.resolve("браузер")
        store.resolve("браузер")
        data = json.loads(store.path.read_text(encoding="utf-8"))
        # 1 (learn) + 2 (resolves) = 3
        assert data["браузер"]["count"] == 3

    def test_get_all(self, store) -> None:
        store.learn("браузер", "chrome")
        store.learn("редактор", "code")
        all_aliases = store.get_all()
        assert all_aliases == {"браузер": "chrome", "редактор": "code"}

    def test_prune_removes_least_used(self, store) -> None:
        # Fill beyond MAX_ALIASES
        for i in range(MAX_ALIASES + 10):
            store.learn(f"alias_{i}", f"target_{i}")

        assert store.alias_count() <= MAX_ALIASES

    def test_prune_keeps_most_used(self, store) -> None:
        # Create many aliases, bump one to high count
        for i in range(MAX_ALIASES + 5):
            store.learn(f"alias_{i}", f"target_{i}")

        # Bump one alias count
        for _ in range(50):
            store.learn("important_alias", "important_target")

        # The important one should survive pruning
        assert store.resolve("important_alias") == "important_target"

    def test_persistence(self, tmp_path) -> None:
        path = tmp_path / "aliases.json"

        # Write
        store1 = PersonalAliasStore(path=path)
        store1.learn("браузер", "chrome")
        store1.learn("редактор", "code")

        # Read in new instance
        store2 = PersonalAliasStore(path=path)
        assert store2.resolve("браузер") == "chrome"
        assert store2.resolve("редактор") == "code"
        assert store2.alias_count() == 2

    def test_corrupted_file_starts_fresh(self, tmp_path) -> None:
        path = tmp_path / "aliases.json"
        path.write_text("not json!!!", encoding="utf-8")

        store = PersonalAliasStore(path=path)
        assert store.alias_count() == 0
        # Should still work
        store.learn("test", "target")
        assert store.resolve("test") == "target"

    def test_resolve_empty_string_returns_none(self, store) -> None:
        store.learn("test", "target")
        assert store.resolve("") is None
        assert store.resolve("   ") is None

    def test_multiple_aliases_same_target(self, store) -> None:
        store.learn("браузер", "chrome")
        store.learn("browser", "chrome")
        store.learn("хром", "chrome")
        assert store.alias_count() == 3
        assert store.resolve("браузер") == "chrome"
        assert store.resolve("browser") == "chrome"
        assert store.resolve("хром") == "chrome"
