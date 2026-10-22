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

import pytest

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

_JARVIS_ROOT = Path(__file__).resolve().parent.parent / "jarvis"

# The single file allowed to touch win32 (relative to _JARVIS_ROOT).
_WIN32_ALLOWED = {Path("platform") / "windows.py"}

# PySide6 is allowed anywhere under ui/ (relative to _JARVIS_ROOT).
_PYSIDE_ALLOWED_DIR = Path("ui")


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
