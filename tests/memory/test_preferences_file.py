"""my_preferences.yaml: empty template in the data dir, user data never overwritten."""

from __future__ import annotations

import yaml

from jarvis.memory.preferences_file import (
    PREFERENCES_FILENAME,
    PREFERENCES_TEMPLATE,
    default_preferences_path,
    ensure_preferences_file,
)


def test_default_path_is_in_data_dir_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert default_preferences_path() == tmp_path / "memory" / PREFERENCES_FILENAME


def test_creates_template_when_missing(tmp_path):
    target = tmp_path / "memory" / PREFERENCES_FILENAME
    assert ensure_preferences_file(target) is True
    assert target.read_text(encoding="utf-8") == PREFERENCES_TEMPLATE


def test_never_overwrites_existing_file(tmp_path):
    target = tmp_path / PREFERENCES_FILENAME
    target.write_text("facts:\n  - content: mine\n", encoding="utf-8")
    assert ensure_preferences_file(target) is False
    assert target.read_text(encoding="utf-8") == "facts:\n  - content: mine\n"


def test_template_is_valid_yaml_without_personal_data():
    data = yaml.safe_load(PREFERENCES_TEMPLATE)
    assert data == {"preferences": {}, "facts": [], "projects": []}


def test_failure_is_non_fatal(tmp_path):
    # Parent "directory" is a file -> mkdir fails; must return False, not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert ensure_preferences_file(blocker / PREFERENCES_FILENAME) is False


def test_importer_creates_template_instead_of_failing(monkeypatch, tmp_path):
    from jarvis.personal_starter.import_starter import import_starter

    target = tmp_path / PREFERENCES_FILENAME
    assert import_starter(yaml_path=str(target), stores_dir=str(tmp_path / "stores")) is False
    assert target.read_text(encoding="utf-8") == PREFERENCES_TEMPLATE
