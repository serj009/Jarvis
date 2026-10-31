"""Launcher-game discovery across storefronts (Task 8c).

A uniform index over installed games from multiple launchers so a single
``launch_game`` tool can resolve a spoken title and start it. Steam is
already handled in ``windows_apps.py`` (kept as-is); this module adds the
other launchers behind a common interface and the Steam source adapts to
the same shape.

Design & security
-----------------
Launcher data (JSON manifests, registry values) is UNTRUSTED external
input. This module never executes strings from it. A game carries a
``LaunchSpec`` that is one of exactly two safe shapes:

* ``URI`` — a launcher protocol deep link built ONLY from identifiers that
  each matched ``_ID_RE`` (``[A-Za-z0-9_.-]+``); colons joined as ``%3A``.
  Launched via ``windows.launch_uri`` (ShellExecuteW, no shell).
* ``EXECUTABLE`` — a direct ``.exe`` that resolves to a path *inside* its
  install directory (verified in ``windows.launch_game_executable`` with
  ``resolve`` + ``is_relative_to``). No arguments from the registry or
  manifest are ever forwarded.

Game titles (``DisplayName`` / ``gameName``) are untrusted text: cleaned
via ``_clean_title`` (length cap, control chars stripped) before use for
matching or speech. They are NOT injected into any LLM prompt here.

Each source is isolated: a parse error in one launcher is logged at
WARNING with its cause and never breaks the other sources or Steam.

Platform specifics (registry, %ProgramData% layout) live in
``jarvis/platform/`` per the architecture rule. The pure parsing helpers
are importable and testable without Windows; filesystem/registry access is
injected so tests run on fixtures.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from jarvis.platform.app_discovery import normalize_query

log = logging.getLogger(__name__)

# Identifier charset allowed inside a launcher URI component (Task 8c
# security requirement). Anything outside this set means the entry is not
# trustworthy enough to build a protocol URI from, and the caller falls
# back to a direct-exe launch or skips it.
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Untrusted-title hygiene: cap length and drop control characters so a
# crafted manifest can't blow up logs/UI or smuggle terminal escapes.
_MAX_TITLE_LEN = 200


class Launcher(StrEnum):
    STEAM = "steam"
    EPIC = "epic"
    GOG = "gog"
    BATTLE_NET = "battlenet"


class LaunchKind(StrEnum):
    URI = "uri"
    EXECUTABLE = "executable"


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """How to start a game — exactly one safe mechanism.

    kind == URI: ``uri`` is a fully-formed, percent-encoded protocol link.
    kind == EXECUTABLE: ``exe`` is an absolute .exe path and ``install_dir``
    its install root; the launcher re-verifies containment before spawning.
    """

    kind: LaunchKind
    uri: str | None = None
    exe: str | None = None
    install_dir: str | None = None


@dataclass(frozen=True, slots=True)
class Game:
    name: str
    launcher: Launcher
    launch_spec: LaunchSpec


@runtime_checkable
class GameSource(Protocol):
    """A storefront that can enumerate its installed games."""

    launcher: Launcher

    def scan(self) -> list[Game]:
        """Return installed games. Never raises: on any error it logs a
        WARNING and returns what it could read (possibly empty)."""
        ...


# --- shared helpers (pure, testable without Windows) ---------------------


def _clean_title(raw: object) -> str:
    """Coerce an untrusted title to a safe display/match string.

    Non-strings -> "". Control characters removed. Collapsed whitespace.
    Length capped. Returns "" when nothing usable remains."""
    if not isinstance(raw, str):
        return ""
    # Drop C0/C1 control chars (incl. CR/LF/NUL) and DEL.
    cleaned = "".join(
        ch for ch in raw if ord(ch) >= 0x20 and ord(ch) != 0x7F
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:_MAX_TITLE_LEN]


def _valid_id(value: object) -> str | None:
    """Return the value if it is a string matching _ID_RE, else None."""
    if isinstance(value, str) and _ID_RE.match(value):
        return value
    return None


def build_epic_uri(namespace: str, catalog_item_id: str, app_name: str) -> str | None:
    """Build the Epic launch deep link from three identifiers, or None if
    any fails ``_ID_RE`` validation.

    Format (confirmed against Epic's official Protocol Activation docs and
    the shortcuts the launcher itself creates)::

        com.epicgames.launcher://apps/<Namespace>%3A<CatalogItemId>%3A<AppName>?action=launch&silent=true

    Only the colons are encoded (%3A); the ids are already restricted to
    [A-Za-z0-9_.-], which need no further percent-encoding."""
    ns = _valid_id(namespace)
    cid = _valid_id(catalog_item_id)
    app = _valid_id(app_name)
    if not (ns and cid and app):
        return None
    return (
        "com.epicgames.launcher://apps/"
        f"{ns}%3A{cid}%3A{app}?action=launch&silent=true"
    )


def parse_epic_manifest(text: str) -> Game | None:
    """Parse one Epic ``*.item`` manifest (JSON) into a Game, or None.

    Primary launch is the protocol URI built from CatalogNamespace /
    CatalogItemId / AppName. If any id fails validation, fall back to a
    direct-exe launch from InstallLocation + LaunchExecutable. If neither
    is usable, return None (caller skips the entry)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    name = _clean_title(data.get("DisplayName"))
    if not name:
        return None

    namespace = data.get("CatalogNamespace")
    catalog_item_id = data.get("CatalogItemId")
    app_name = data.get("AppName")
    uri = None
    if (
        isinstance(namespace, str)
        and isinstance(catalog_item_id, str)
        and isinstance(app_name, str)
    ):
        uri = build_epic_uri(namespace, catalog_item_id, app_name)

    if uri is not None:
        return Game(
            name=name,
            launcher=Launcher.EPIC,
            launch_spec=LaunchSpec(kind=LaunchKind.URI, uri=uri),
        )

    # Fallback: direct .exe inside InstallLocation. Some Epic games need
    # the launcher and may not start this way — the launcher logs a clear
    # warning. If the exe can't be located, skip the entry.
    install_location = data.get("InstallLocation")
    launch_exe = data.get("LaunchExecutable")
    if not (isinstance(install_location, str) and isinstance(launch_exe, str)):
        return None
    install_location = install_location.strip()
    launch_exe = launch_exe.strip()
    if not install_location or not launch_exe:
        return None
    # LaunchExecutable is relative to InstallLocation (e.g.
    # "MyGame/Binaries/Win64/MyGame.exe"). Join without resolving here; the
    # launcher resolves + verifies containment at launch time.
    exe_path = str(Path(install_location) / launch_exe)
    if not exe_path.lower().endswith(".exe"):
        return None
    log.warning(
        "Epic game %r has no valid protocol ids; falling back to direct "
        "exe launch (%s) -- some Epic titles require the launcher and may "
        "not start this way.",
        name, exe_path,
    )
    return Game(
        name=name,
        launcher=Launcher.EPIC,
        launch_spec=LaunchSpec(
            kind=LaunchKind.EXECUTABLE,
            exe=exe_path,
            install_dir=install_location,
        ),
    )


def _scan_epic_manifest_dir(manifest_dir: Path) -> list[Game]:
    """Parse every ``*.item`` in a directory. Per-file errors are isolated
    (logged WARNING) so one bad manifest can't hide the rest."""
    games: list[Game] = []
    try:
        entries = sorted(manifest_dir.glob("*.item"))
    except OSError as e:
        log.warning("Epic: cannot list manifests in %s: %s", manifest_dir, e)
        return games
    for item in entries:
        try:
            text = item.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("Epic: cannot read manifest %s: %s", item, e)
            continue
        game = parse_epic_manifest(text)
        if game is not None:
            games.append(game)
    return games


def _epic_manifests_dir() -> Path | None:
    """Default Epic manifests dir: %ProgramData%\\Epic\\EpicGamesLauncher\\
    Data\\Manifests. Returns None if %ProgramData% is unset or missing."""
    import os

    program_data = os.environ.get("ProgramData", "")
    if not program_data:
        return None
    path = Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    return path if path.is_dir() else None


class EpicGamesSource:
    """Discover installed Epic Games Store titles from .item manifests."""

    launcher = Launcher.EPIC

    def __init__(self, manifest_dir: Path | None = None) -> None:
        # Injectable for tests; None -> resolve the real dir at scan time.
        self._manifest_dir = manifest_dir

    def scan(self) -> list[Game]:
        manifest_dir = self._manifest_dir
        if manifest_dir is None:
            manifest_dir = _epic_manifests_dir()
        if manifest_dir is None:
            return []
        try:
            return _scan_epic_manifest_dir(manifest_dir)
        except Exception as e:  # defensive: a source must never break others
            log.warning("Epic source scan failed: %s", e)
            return []


def index_games(sources: Iterable[GameSource]) -> dict[str, Game]:
    """Build a normalized-title -> Game index across sources.

    Keys are produced by ``normalize_query`` (same mechanism open_app uses,
    incl. RU/UK transliteration). First writer wins on collision so earlier
    sources take precedence; a source raising is isolated and skipped."""
    index: dict[str, Game] = {}
    for source in sources:
        try:
            games = source.scan()
        except Exception as e:
            log.warning("game source %r scan failed: %s", source, e)
            continue
        for game in games:
            key = normalize_query(game.name)
            if key and key not in index:
                index[key] = game
    return index
