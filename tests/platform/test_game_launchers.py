"""Tests for jarvis.platform.game_launchers — launcher-game discovery.

Pure parsing/indexing logic on fixtures; no Windows, no registry, no real
launchers. Runs in .test_venv. Real launch + registry reads are verified
at home ("проверить дома").
"""

from __future__ import annotations

import json

from jarvis.platform.game_launchers import (
    EpicGamesSource,
    Game,
    Launcher,
    LaunchKind,
    build_epic_uri,
    index_games,
    parse_epic_manifest,
)

# --- build_epic_uri ------------------------------------------------------


def test_build_epic_uri_valid_ids():
    uri = build_epic_uri("fn", "4fe75bbc5a674f4f9b356b5c90567da5", "Fortnite")
    assert uri == (
        "com.epicgames.launcher://apps/"
        "fn%3A4fe75bbc5a674f4f9b356b5c90567da5%3AFortnite"
        "?action=launch&silent=true"
    )


def test_build_epic_uri_allows_dot_dash_underscore():
    assert build_epic_uri("ns.1", "id-2", "App_3") is not None


def test_build_epic_uri_rejects_bad_id():
    # Each component must match [A-Za-z0-9_.-]+ ; a colon / space / slash
    # must fail so no extra URI structure can be injected.
    assert build_epic_uri("fn", "id", "app!") is None
    assert build_epic_uri("ns:bad", "id", "app") is None
    assert build_epic_uri("fn", "id 2", "app") is None
    assert build_epic_uri("fn", "id/x", "app") is None
    assert build_epic_uri("", "id", "app") is None


# --- parse_epic_manifest -------------------------------------------------


def test_parse_epic_manifest_builds_uri_game():
    text = json.dumps({
        "DisplayName": "Fortnite",
        "CatalogNamespace": "fn",
        "CatalogItemId": "4fe75bbc5a674f4f9b356b5c90567da5",
        "AppName": "Fortnite",
        "InstallLocation": "C:/Games/Fortnite",
        "LaunchExecutable": "Fortnite.exe",
    })
    game = parse_epic_manifest(text)
    assert isinstance(game, Game)
    assert game.launcher is Launcher.EPIC
    assert game.launch_spec.kind is LaunchKind.URI
    assert game.launch_spec.uri is not None
    assert game.launch_spec.uri.startswith("com.epicgames.launcher://apps/")


def test_parse_epic_manifest_falls_back_to_exe_on_bad_id():
    text = json.dumps({
        "DisplayName": "My Game",
        "CatalogNamespace": "ns:bad",  # invalid -> no URI
        "CatalogItemId": "id",
        "AppName": "app",
        "InstallLocation": "C:/Games/MyGame",
        "LaunchExecutable": "bin/MyGame.exe",
    })
    game = parse_epic_manifest(text)
    assert game is not None
    assert game.launch_spec.kind is LaunchKind.EXECUTABLE
    assert game.launch_spec.exe is not None
    assert game.launch_spec.exe.lower().endswith("mygame.exe")
    assert game.launch_spec.install_dir == "C:/Games/MyGame"


def test_parse_epic_manifest_no_display_name_returns_none():
    text = json.dumps({"CatalogNamespace": "a", "CatalogItemId": "b", "AppName": "c"})
    assert parse_epic_manifest(text) is None


def test_parse_epic_manifest_bad_json_returns_none():
    assert parse_epic_manifest("{not valid json") is None
    assert parse_epic_manifest("[]") is None
    assert parse_epic_manifest('"a string"') is None


def test_parse_epic_manifest_cleans_untrusted_title():
    raw_name = "Half\x00Life\n2" + ("x" * 300)
    text = json.dumps({
        "DisplayName": raw_name,
        "CatalogNamespace": "a", "CatalogItemId": "b", "AppName": "c",
    })
    game = parse_epic_manifest(text)
    assert game is not None
    assert "\x00" not in game.name
    assert "\n" not in game.name
    assert len(game.name) <= 200


def test_parse_epic_manifest_fallback_requires_exe_suffix():
    text = json.dumps({
        "DisplayName": "X",
        "CatalogNamespace": "n:s",  # force fallback
        "CatalogItemId": "i", "AppName": "a",
        "InstallLocation": "C:/G",
        "LaunchExecutable": "run.bat",  # not .exe -> skipped
    })
    assert parse_epic_manifest(text) is None


# --- EpicGamesSource.scan over a fixture directory -----------------------


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_epic_source_scans_manifest_dir(tmp_path):
    _write(tmp_path / "A.item", {
        "DisplayName": "Alan Wake 2",
        "CatalogNamespace": "aw", "CatalogItemId": "c1", "AppName": "AlanWake2",
    })
    _write(tmp_path / "B.item", {
        "DisplayName": "Control",
        "CatalogNamespace": "ct", "CatalogItemId": "c2", "AppName": "Control",
    })
    # a non-.item file is ignored
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    source = EpicGamesSource(manifest_dir=tmp_path)
    games = source.scan()
    names = sorted(g.name for g in games)
    assert names == ["Alan Wake 2", "Control"]


def test_epic_source_isolates_bad_manifest(tmp_path):
    _write(tmp_path / "good.item", {
        "DisplayName": "Good Game",
        "CatalogNamespace": "g", "CatalogItemId": "c", "AppName": "Good",
    })
    (tmp_path / "bad.item").write_text("{broken json", encoding="utf-8")
    source = EpicGamesSource(manifest_dir=tmp_path)
    games = source.scan()
    # The broken manifest is skipped; the good one still comes through.
    assert [g.name for g in games] == ["Good Game"]


def test_epic_source_missing_dir_returns_empty(tmp_path):
    source = EpicGamesSource(manifest_dir=tmp_path / "does_not_exist")
    assert source.scan() == []


# --- index_games ---------------------------------------------------------


class _FakeSource:
    def __init__(self, launcher, games):
        self.launcher = launcher
        self._games = games

    def scan(self):
        return self._games


def _uri_game(name, launcher=Launcher.EPIC):
    from jarvis.platform.game_launchers import LaunchSpec

    return Game(
        name=name,
        launcher=launcher,
        launch_spec=LaunchSpec(kind=LaunchKind.URI, uri="x://y"),
    )


def test_index_games_normalizes_titles():
    src = _FakeSource(Launcher.EPIC, [_uri_game("Alan Wake 2")])
    index = index_games([src])
    assert "alan wake 2" in index


def test_index_games_first_writer_wins():
    a = _uri_game("Portal", Launcher.STEAM)
    b = _uri_game("Portal", Launcher.EPIC)
    s1 = _FakeSource(Launcher.STEAM, [a])
    s2 = _FakeSource(Launcher.EPIC, [b])
    index = index_games([s1, s2])
    assert index["portal"].launcher is Launcher.STEAM


def test_index_games_isolates_failing_source():
    class _Boom:
        launcher = Launcher.GOG

        def scan(self):
            raise RuntimeError("db locked")

    good = _FakeSource(Launcher.EPIC, [_uri_game("Hades")])
    index = index_games([_Boom(), good])
    assert "hades" in index


def test_index_games_transliterates_cyrillic_titles():
    # normalize_query transliterates, so a Cyrillic-named game is findable
    # under its Latin key (same mechanism open_app uses).
    src = _FakeSource(Launcher.EPIC, [_uri_game("Сталкер")])
    index = index_games([src])
    assert "stalker" in index
