"""Saved copy of the installed-apps index (%APPDATA%\\Jarvis\\installed_apps.json).

All files live in pytest's tmp_path: the user's real APPDATA is never touched."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from jarvis.platform import windows_apps as wa
from jarvis.platform.windows_apps import (
    InstalledApp,
    load_installed_app_index,
    prime_installed_app_index,
    refresh_installed_app_index,
    save_installed_app_index,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="index build is Windows-only")


@pytest.fixture(autouse=True)
def _restore_index_cache():
    saved = (wa._installed_index_cache, wa._installed_index_cached_at)
    yield
    wa._installed_index_cache, wa._installed_index_cached_at = saved


def _sample() -> dict[str, InstalledApp]:
    return {
        "steam": InstalledApp("Steam", r"C:\ProgramData\Microsoft\Windows\Start Menu\Steam.lnk"),
        "telegram": InstalledApp("Telegram Desktop", r"shell:appsFolder\Telegram.Desktop"),
    }


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "Jarvis" / "installed_apps.json"
    save_installed_app_index(_sample(), path)

    assert load_installed_app_index(path) == _sample()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and "scanned_at" in data
    assert not path.with_suffix(".json.tmp").exists()


def test_load_missing_file_returns_none(tmp_path):
    assert load_installed_app_index(tmp_path / "nope.json") is None


@pytest.mark.parametrize("content", ["{broken", '{"version": 99, "apps": []}', "[]"])
def test_load_rejects_bad_or_foreign_files(tmp_path, content):
    path = tmp_path / "installed_apps.json"
    path.write_text(content, encoding="utf-8")
    assert load_installed_app_index(path) is None


def test_load_skips_malformed_entries(tmp_path):
    path = tmp_path / "installed_apps.json"
    path.write_text(json.dumps({"version": 1, "apps": [
        {"key": "steam", "name": "Steam", "launch": "steam.lnk"},
        {"key": "", "launch": "x.exe"},
        {"key": "nolaunch"},
        "garbage",
    ]}), encoding="utf-8")

    assert load_installed_app_index(path) == {"steam": InstalledApp("Steam", "steam.lnk")}


@windows_only
def test_primed_index_is_used_without_scanning():
    prime_installed_app_index(_sample())
    with patch.object(wa, "_scan_start_apps", side_effect=AssertionError("scanned")):
        assert wa.build_installed_app_index() == _sample()


@windows_only
def test_refresh_rescans_and_saves(tmp_path):
    path = tmp_path / "installed_apps.json"
    prime_installed_app_index({"old": InstalledApp("Old", "old.exe")})
    with (
        patch.object(wa, "_scan_app_paths_registry", return_value={}),
        patch.object(wa, "_scan_shortcuts", return_value={}),
        patch.object(wa, "_scan_start_apps", return_value=_sample()),
    ):
        assert refresh_installed_app_index(path) == 2

    assert load_installed_app_index(path) == _sample()
    assert "old" not in wa._installed_index_cache
