"""Architecture-rule guards (SPEC.md § Module boundaries).

Two rules from SPEC.md keep modules swappable:

  1. "No ``win32`` imports outside ``platform/windows.py``."
  2. "No file outside ``ui/`` may import from ``PySide6``."

These are enforced structurally here by walking every ``.py`` file under
``jarvis/`` and parsing it with ``ast`` — so a violation fails CI instead of
silently eroding the boundary.

What counts as a "win32 import":
  * ``import win32gui`` / ``import win32con`` / ``from win32api import ...``
    (the pywin32 family — any module whose top name starts with ``win32``)
  * ``import pywin32`` / ``import pythoncom`` / ``import pywintypes``
  * ``ctypes.windll`` attribute access, and ``from ctypes import windll``
    (``windll`` only exists on the Windows build of ctypes — it is the
    Win32 FFI entry point, so it is Windows-specific even though ``ctypes``
    itself is cross-platform stdlib and stays allowed everywhere).

Plain ``import ctypes`` / ``import winreg`` are NOT flagged on their own:
winreg is stdlib and ctypes is cross-platform. The Windows-specific part is
the ``windll`` loader, which is what we gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

_JARVIS_ROOT = Path(__file__).resolve().parent.parent / "jarvis"

# The single file allowed to touch win32 (relative to _JARVIS_ROOT).
_WIN32_ALLOWED = {Path("platform") / "windows.py"}

# Task 8c: Windows registry access (``winreg`` / ``_winreg``, and registry
# access via ctypes advapi32) is confined to platform/. EXPLICIT file list
# — NOT a whole-directory allowance — so a new file dropped into platform/
# does not silently gain registry access; it must be added here on review.
#   * platform/windows.py       — default-app / startup registry reads+writes.
#   * platform/windows_apps.py  — Steam path + App Paths registry reads.
#   * platform/registry.py      — the read-only RegistryReader seam that
#     game-launcher discovery (GOG) depends on through an interface.
_WINREG_ALLOWED = {
    Path("platform") / "windows.py",
    Path("platform") / "windows_apps.py",
    Path("platform") / "registry.py",
}

# PySide6 is allowed anywhere under ui/ (relative to _JARVIS_ROOT).
_PYSIDE_ALLOWED_DIR = Path("ui")

# ---------------------------------------------------------------------------
# Data-directory rule (Task 4+B)
# ---------------------------------------------------------------------------
#
# Jarvis's own data directory must be resolved in exactly one place —
# ``jarvis/paths.py`` (user_data_dir / data_path).  No other module may build
# a data path ad-hoc via ``os.environ["APPDATA"]`` /
# ``os.environ.get("APPDATA")`` or ``Path.home() / ".jarvis"``.
#
# Explicit allow-list (relative to _JARVIS_ROOT).  None of these build a path
# to Jarvis's OWN data:
#   * paths.py            — the single resolver itself.
#   * core/data_migration — the one-time ~/.jarvis migration module.
#   * platform/windows.py, platform/windows_apps.py — read FOREIGN system
#     folders (shell:startup, Start Menu) that happen to live under %APPDATA%.
#   * tools/local/game_saves.py — expands an {APPDATA} token for FOREIGN game
#     save locations.
#   * dev/audio_loopback.py, ui/settings/tabs/voice.py — resolve the bundled
#     Piper *voices* resource dir (~/.jarvis/voices), not user data.
_DATA_DIR_ALLOWED = {
    Path("paths.py"),
    Path("core") / "data_migration.py",
    Path("platform") / "windows.py",
    Path("platform") / "windows_apps.py",
    Path("tools") / "local" / "game_saves.py",
    Path("dev") / "audio_loopback.py",
    Path("ui") / "settings" / "tabs" / "voice.py",
}


def _iter_python_files() -> list[Path]:
    return sorted(_JARVIS_ROOT.rglob("*.py"))


def _rel(path: Path) -> Path:
    return path.relative_to(_JARVIS_ROOT)


def _module_top(name: str | None) -> str:
    """Top-level package name of a dotted import (``win32api.foo`` -> ``win32api``)."""
    if not name:
        return ""
    return name.split(".")[0]


# ---------------------------------------------------------------------------
# AST scanners
# ---------------------------------------------------------------------------

def _is_win32_module(top: str) -> bool:
    if top.startswith("win32"):
        return True
    return top in {"pywin32", "pythoncom", "pywintypes"}


def _win32_violations(tree: ast.AST) -> list[str]:
    """Return human-readable descriptions of win32 usages in *tree*."""
    hits: list[str] = []
    for node in ast.walk(tree):
        # import win32gui / import pythoncom / ...
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _module_top(alias.name)
                if _is_win32_module(top):
                    hits.append(f"line {node.lineno}: import {alias.name}")
        # from win32api import ... / from ctypes import windll
        elif isinstance(node, ast.ImportFrom):
            top = _module_top(node.module)
            if _is_win32_module(top):
                hits.append(f"line {node.lineno}: from {node.module} import ...")
            elif top == "ctypes":
                for alias in node.names:
                    if alias.name == "windll":
                        hits.append(
                            f"line {node.lineno}: from ctypes import windll"
                        )
        # ctypes.windll.user32 (attribute access)
        elif isinstance(node, ast.Attribute):
            if node.attr == "windll" and isinstance(node.value, ast.Name):
                if node.value.id == "ctypes":
                    hits.append(f"line {node.lineno}: ctypes.windll")
    return hits


def _winreg_violations(tree: ast.AST) -> list[str]:
    """Return descriptions of Windows-registry access in *tree*.

    Flags ``import winreg`` / ``import _winreg`` / ``from winreg import ...``
    and registry access through ``ctypes`` advapi32 (``ctypes.windll.advapi32``
    attribute access or ``ctypes.WinDLL("advapi32")``). Plain ``import ctypes``
    is not flagged — only an advapi32 reference is."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _module_top(alias.name)
                if top in {"winreg", "_winreg"}:
                    hits.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top = _module_top(node.module)
            if top in {"winreg", "_winreg"}:
                hits.append(f"line {node.lineno}: from {node.module} import ...")
        # ctypes.windll.advapi32  (attribute access on a windll chain)
        elif isinstance(node, ast.Attribute):
            if node.attr == "advapi32":
                hits.append(f"line {node.lineno}: ctypes advapi32 access")
        # ctypes.WinDLL("advapi32") / windll.LoadLibrary("advapi32.dll")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if low in {"advapi32", "advapi32.dll"}:
                hits.append(f"line {node.lineno}: advapi32 library reference")
    return hits


def _pyside_violations(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_top(alias.name) == "PySide6":
                    hits.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if _module_top(node.module) == "PySide6":
                hits.append(f"line {node.lineno}: from {node.module} import ...")
    return hits


def _is_os_environ(node: ast.AST) -> bool:
    """True if *node* is the ``os.environ`` attribute access."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _data_dir_violations(tree: ast.AST) -> list[str]:
    """Ad-hoc data-dir construction: APPDATA env reads or ".jarvis" literals."""
    hits: list[str] = []
    for node in ast.walk(tree):
        # os.environ.get("APPDATA" [, ...])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_os_environ(node.func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "APPDATA"
        ):
            hits.append(f'line {node.lineno}: os.environ.get("APPDATA")')
        # os.environ["APPDATA"]
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value == "APPDATA":
                hits.append(f'line {node.lineno}: os.environ["APPDATA"]')
        # a bare ".jarvis" string literal (e.g. Path.home() / ".jarvis")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == ".jarvis"
        ):
            hits.append(f'line {node.lineno}: ".jarvis" literal')
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_jarvis_package_exists() -> None:
    """Sanity: the walk root resolves and contains Python files."""
    assert _JARVIS_ROOT.is_dir(), f"missing jarvis package at {_JARVIS_ROOT}"
    assert _iter_python_files(), "no .py files found under jarvis/"


def test_no_win32_imports_outside_platform_windows() -> None:
    """SPEC: win32 (pywin32 + ctypes.windll) only in platform/windows.py."""
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        rel = _rel(path)
        if rel in _WIN32_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _win32_violations(tree)
        if hits:
            offenders[str(rel)] = hits

    assert not offenders, (
        "win32 imports/usages found outside jarvis/platform/windows.py "
        "(SPEC.md § Module boundaries):\n"
        + "\n".join(
            f"  {f}:\n" + "\n".join(f"    {h}" for h in hs)
            for f, hs in sorted(offenders.items())
        )
    )


def test_no_winreg_imports_outside_allow_list() -> None:
    """Task 8c: Windows registry access confined to the explicit file list.

    ``winreg`` is stdlib and not win32 per se, but registry access is a
    platform-specific capability we keep inside platform/ behind the
    RegistryReader seam so the rest of the codebase stays testable and
    OS-agnostic."""
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        rel = _rel(path)
        if rel in _WINREG_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _winreg_violations(tree)
        if hits:
            offenders[str(rel)] = hits

    assert not offenders, (
        "Windows registry access found outside the allow-list "
        "(route through jarvis/platform/registry.py RegistryReader):\n"
        + "\n".join(
            f"  {f}:\n" + "\n".join(f"    {h}" for h in hs)
            for f, hs in sorted(offenders.items())
        )
    )


def test_winreg_rule_detects_violations() -> None:
    """The winreg scanner flags each forbidden form (negative case)."""
    import_winreg = ast.parse("import winreg\n")
    import_underscore = ast.parse("import _winreg\n")
    from_winreg = ast.parse("from winreg import OpenKey, HKEY_LOCAL_MACHINE\n")
    advapi_attr = ast.parse(
        "import ctypes\n"
        "def f():\n"
        "    return ctypes.windll.advapi32.RegOpenKeyExW\n"
    )
    advapi_lib = ast.parse('import ctypes\nd = ctypes.WinDLL("advapi32")\n')
    clean = ast.parse(
        "from jarvis.platform.registry import WindowsRegistryReader\n"
        "def f():\n"
        "    return WindowsRegistryReader().read_values('HKLM', 'X')\n"
    )

    assert _winreg_violations(import_winreg), "should flag 'import winreg'"
    assert _winreg_violations(import_underscore), "should flag 'import _winreg'"
    assert _winreg_violations(from_winreg), "should flag 'from winreg import ...'"
    assert _winreg_violations(advapi_attr), "should flag ctypes advapi32 access"
    assert _winreg_violations(advapi_lib), "should flag advapi32 library ref"
    assert not _winreg_violations(clean), "RegistryReader usage must not be flagged"


def test_winreg_allow_list_entries_exist() -> None:
    """Every winreg allow-listed file must exist (stale entries mask drift)."""
    missing = [str(rel) for rel in _WINREG_ALLOWED if not (_JARVIS_ROOT / rel).is_file()]
    assert not missing, f"stale _WINREG_ALLOWED entries (file missing): {missing}"


def test_no_pyside_imports_outside_ui() -> None:
    """SPEC: PySide6 only under jarvis/ui/."""
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        rel = _rel(path)
        # Allowed anywhere inside ui/ (rel path starts with "ui/").
        if _PYSIDE_ALLOWED_DIR in rel.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _pyside_violations(tree)
        if hits:
            offenders[str(rel)] = hits

    assert not offenders, (
        "PySide6 imports found outside jarvis/ui/ "
        "(SPEC.md § Module boundaries):\n"
        + "\n".join(
            f"  {f}:\n" + "\n".join(f"    {h}" for h in hs)
            for f, hs in sorted(offenders.items())
        )
    )


def test_no_adhoc_data_dir_outside_paths() -> None:
    """Task 4+B: data dir resolved only in paths.py (+ explicit allow-list).

    No module may build Jarvis's own data path ad-hoc via APPDATA env reads
    or a ``".jarvis"`` literal — everything routes through
    ``jarvis.paths.user_data_dir`` / ``data_path``.
    """
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        rel = _rel(path)
        if rel in _DATA_DIR_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _data_dir_violations(tree)
        if hits:
            offenders[str(rel)] = hits

    assert not offenders, (
        "ad-hoc data-dir construction found outside jarvis/paths.py "
        "(Task 4+B — route through user_data_dir()/data_path()):\n"
        + "\n".join(
            f"  {f}:\n" + "\n".join(f"    {h}" for h in hs)
            for f, hs in sorted(offenders.items())
        )
    )


def test_data_dir_rule_detects_violations() -> None:
    """The data-dir scanner flags each forbidden construction (negative case)."""
    env_get = ast.parse(
        "import os\n"
        "from pathlib import Path\n"
        "def f():\n"
        "    return Path(os.environ.get('APPDATA')) / 'Jarvis'\n"
    )
    env_sub = ast.parse(
        "import os\n"
        "from pathlib import Path\n"
        "def f():\n"
        "    return Path(os.environ['APPDATA']) / 'Jarvis'\n"
    )
    dotjarvis = ast.parse(
        "from pathlib import Path\n"
        "def f():\n"
        "    return Path.home() / '.jarvis'\n"
    )
    clean = ast.parse(
        "from jarvis.paths import data_path\n"
        "def f():\n"
        "    return data_path('config.json')\n"
    )

    assert _data_dir_violations(env_get), "should flag os.environ.get('APPDATA')"
    assert _data_dir_violations(env_sub), "should flag os.environ['APPDATA']"
    assert _data_dir_violations(dotjarvis), "should flag a '.jarvis' literal"
    assert not _data_dir_violations(clean), "data_path() usage must not be flagged"


def test_data_dir_allow_list_entries_exist() -> None:
    """Every allow-listed file must exist — stale entries would mask drift."""
    missing = [str(rel) for rel in _DATA_DIR_ALLOWED if not (_JARVIS_ROOT / rel).is_file()]
    assert not missing, f"stale _DATA_DIR_ALLOWED entries (file missing): {missing}"
