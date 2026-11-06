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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from jarvis.platform.app_discovery import normalize_query

if TYPE_CHECKING:
    from jarvis.platform.windows_apps import SteamGame
from jarvis.platform.registry import HKLM, RegistryReader

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


# --- GOG Galaxy (registry) ----------------------------------------------

# Per-game install info lives under this 32-bit-view key, one subkey per
# installed game id. Values of interest: gameName (title), exe (full path
# to the game executable), path (install directory). No official Galaxy
# launch URI could be confirmed, so games are launched by direct .exe
# inside their install dir (Task 8c requirement 6).
_GOG_GAMES_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def parse_gog_game_values(values: dict[str, object]) -> Game | None:
    """Build a GOG Game from one game key's registry values, or None.

    ``values`` is whatever RegistryReader.read_values returned for a single
    ``...\\GOG.com\\Games\\<id>`` key. All fields are untrusted: the title
    is cleaned; exe/path are passed as data to the launcher, which verifies
    containment at launch time. Registry-provided arguments are ignored."""
    raw_name = values.get("gameName")
    name = _clean_title(raw_name)
    if not name:
        return None
    exe = values.get("exe")
    path = values.get("path")
    if not (isinstance(exe, str) and isinstance(path, str)):
        return None
    exe = exe.strip()
    path = path.strip()
    if not exe or not path:
        return None
    if not exe.lower().endswith(".exe"):
        return None
    return Game(
        name=name,
        launcher=Launcher.GOG,
        launch_spec=LaunchSpec(
            kind=LaunchKind.EXECUTABLE,
            exe=exe,
            install_dir=path,
        ),
    )


def scan_gog_games(reader: RegistryReader) -> list[Game]:
    """Enumerate installed GOG games via the registry reader.

    Per-game errors are isolated: a malformed/partial game key is skipped,
    not fatal. The reader never raises (it returns empty on error), so a
    missing GOG install simply yields []."""
    games: list[Game] = []
    game_ids = reader.read_subkeys(HKLM, _GOG_GAMES_KEY)
    for game_id in game_ids:
        # Only simple id subkeys; defend against odd names.
        values = reader.read_values(HKLM, f"{_GOG_GAMES_KEY}\\{game_id}")
        if not values:
            continue
        game = parse_gog_game_values(values)
        if game is not None:
            games.append(game)
    return games


class GogGamesSource:
    """Discover installed GOG Galaxy games from the registry."""

    launcher = Launcher.GOG

    def __init__(self, reader: RegistryReader | None = None) -> None:
        # Injectable for tests; None -> real WindowsRegistryReader at scan.
        self._reader = reader

    def scan(self) -> list[Game]:
        reader = self._reader
        if reader is None:
            from jarvis.platform.registry import WindowsRegistryReader

            reader = WindowsRegistryReader()
        try:
            return scan_gog_games(reader)
        except Exception as e:  # defensive: a source must never break others
            log.warning("GOG source scan failed: %s", e)
            return []


# --- Steam (adapter over the existing appmanifest parser) ----------------


def steam_game_to_game(name: str, app_id: int) -> Game | None:
    """Adapt a parsed Steam library title into a unified Game.

    Launch is the steam://run/<id> protocol URI (same handler
    launch_steam_game uses). app_id is an int so the URI carries no
    injectable text; _clean_title guards the display name."""
    clean = _clean_title(name)
    if not clean:
        return None
    try:
        aid = int(app_id)
    except (TypeError, ValueError):
        return None
    return Game(
        name=clean,
        launcher=Launcher.STEAM,
        launch_spec=LaunchSpec(kind=LaunchKind.URI, uri=f"steam://run/{aid}"),
    )


class SteamGamesSource:
    """Discover installed Steam games, reusing the existing appmanifest
    parser in windows_apps (no duplication). Steam becomes one source in
    the unified index so launch_game finds Steam titles too."""

    launcher = Launcher.STEAM

    def __init__(
        self, games: dict[str, SteamGame] | None = None
    ) -> None:
        # Injectable for tests: a pre-built {norm_name: SteamGame} map.
        self._games = games

    def scan(self) -> list[Game]:
        games_map = self._games
        if games_map is None:
            from jarvis.platform.windows_apps import scan_steam_games

            games_map = scan_steam_games()
        out: list[Game] = []
        for sg in games_map.values():
            game = steam_game_to_game(sg.name, sg.app_id)
            if game is not None:
                out.append(game)
        return out


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


# --- priority-ordered assembly + launch ---------------------------------

_DEFAULT_LAUNCHER_PRIORITY = ("steam", "epic", "gog")


def _sources_in_priority_order(
    priority: list[str] | tuple[str, ...] | None,
) -> list[GameSource]:
    """Build the source list ordered by launcher priority.

    first-writer-wins in index_games then resolves a multi-launcher title
    to the highest-priority launcher that has it. Unknown names in the
    priority list are ignored; missing ones are appended in default order
    so a typo can't silently drop a launcher."""
    order = list(priority) if priority else list(_DEFAULT_LAUNCHER_PRIORITY)
    for name in _DEFAULT_LAUNCHER_PRIORITY:
        if name not in order:
            order.append(name)
    factories: dict[str, type] = {
        "steam": SteamGamesSource,
        "epic": EpicGamesSource,
        "gog": GogGamesSource,
    }
    sources: list[GameSource] = []
    for name in order:
        factory = factories.get(name)
        if factory is not None:
            sources.append(factory())
    return sources


def build_game_index(
    priority: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Game]:
    """Scan every launcher (in priority order) into one normalized index.

    Safe to call off the hot path / in a background thread: each source is
    isolated, so a failing or unavailable launcher never breaks the rest."""
    return index_games(_sources_in_priority_order(priority))


def launch_game_spec(spec: LaunchSpec) -> None:
    """Execute a LaunchSpec via the no-shell platform launchers.

    URI -> windows.launch_uri (ShellExecuteW). EXECUTABLE ->
    windows.launch_game_executable (direct .exe, resolve + containment).
    Raises on an invalid/placeholder spec so callers can report failure."""
    if spec.kind is LaunchKind.URI:
        if not spec.uri:
            raise ValueError("URI launch spec has no uri")
        from jarvis.platform import windows as winplat

        winplat.launch_uri(spec.uri)
        return
    if spec.kind is LaunchKind.EXECUTABLE:
        if not (spec.exe and spec.install_dir):
            raise ValueError("EXECUTABLE launch spec missing exe/install_dir")
        from jarvis.platform import windows as winplat

        winplat.launch_game_executable(spec.exe, spec.install_dir)
        return
    raise ValueError(f"unknown launch spec kind: {spec.kind!r}")
