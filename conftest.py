"""Project-wide pytest configuration.

Keeps test infra concerns (temp-dir safety, global fixtures) out of
individual test modules and pyproject.toml addopts.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path


def pytest_configure(config):
    """Redirect tmp_path to a project-local directory.

    On Windows, pytest's default temp root (``%TEMP%/pytest-of-<user>``)
    can become inaccessible when a previous run created it under an
    elevated or different security context (WinError 5 — Access is denied).

    By setting ``basetemp`` to ``.pytest_tmp`` inside the project tree
    (when no explicit ``--basetemp`` was passed on the CLI) we sidestep
    the OS-level permission issue entirely.
    """
    if config.option.basetemp is None:
        fallback = Path(config.rootpath) / ".pytest_tmp"
        fallback.mkdir(exist_ok=True)
        config.option.basetemp = str(fallback)


# ---------------------------------------------------------------------------
# Auto-skip tests that import unavailable hardware/optional modules
# ---------------------------------------------------------------------------
# These modules require hardware or heavy native libs that are not installed
# in lightweight test virtualenvs (.test_venv on a work PC, CI without GPU).
# Instead of scattering pytest.importorskip() in dozens of test files, we
# probe the test file's top-level imports BEFORE pytest tries to collect it.
# If any import resolves to a missing optional package, we skip the file.

_OPTIONAL_PACKAGES = {
    "sounddevice",
    "openwakeword",
    "faster_whisper",
    "piper",            # piper-tts
    "silero_vad",
    "sentence_transformers",
    "selenium",
    "onnxruntime",
    "PySide6",
}

# Cache: module_name -> bool (available)
_import_cache: dict[str, bool] = {}


def _is_available(mod_name: str) -> bool:
    """Check if a top-level package is importable (cached)."""
    if mod_name not in _import_cache:
        _import_cache[mod_name] = importlib.util.find_spec(mod_name) is not None
    return _import_cache[mod_name]


def _extract_top_level_imports(filepath: Path) -> set[str]:
    """Parse a Python file's AST and return top-level imported package names."""
    try:
        tree = ast.parse(filepath.read_bytes(), filename=str(filepath))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def pytest_ignore_collect(collection_path, config):
    """Skip test files whose top-level imports need unavailable packages.

    We trace the import chain one level deep: if the test file imports
    ``from jarvis.foo import X`` and ``jarvis/foo.py`` itself does
    ``import sounddevice``, we detect that transitively.
    """
    if not collection_path.suffix == ".py":
        return None

    # Direct imports in the test file
    direct = _extract_top_level_imports(collection_path)
    missing = _OPTIONAL_PACKAGES & direct
    if missing:
        mod = next(iter(missing))
        if not _is_available(mod):
            return True  # skip


    # One-level transitive: test imports jarvis.X → check jarvis/X.py
    root = config.rootpath
    for pkg in direct:
        if pkg != "jarvis":
            continue
        # Re-parse to get full dotted names
        try:
            tree = ast.parse(collection_path.read_bytes())
        except SyntaxError:
            break
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("jarvis."):
                parts = node.module.split(".")
                # Resolve to file: jarvis.audio.tts -> jarvis/audio/tts.py
                candidate = root / Path(*parts).with_suffix(".py")
                if candidate.exists():
                    sub_imports = _extract_top_level_imports(candidate)
                    # Check both direct optional deps AND their transitive deps (2 levels)
                    sub_missing = _OPTIONAL_PACKAGES & sub_imports
                    # Also recurse one more level for jarvis.X -> jarvis.Y -> optional
                    sub_missing |= _check_transitive(candidate, root)
                    for mod in sub_missing:
                        if not _is_available(mod):
                            return True  # skip
        break

    return None  # don't skip


def _check_transitive(filepath: Path, root: Path) -> set[str]:
    """Check one more level of imports for optional packages."""
    result: set[str] = set()
    try:
        tree = ast.parse(filepath.read_bytes(), filename=str(filepath))
    except SyntaxError:
        return result
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("jarvis."):
            parts = node.module.split(".")
            candidate = root / Path(*parts).with_suffix(".py")
            if candidate.exists():
                sub_imports = _extract_top_level_imports(candidate)
                result |= _OPTIONAL_PACKAGES & sub_imports
    return result
