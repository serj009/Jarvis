"""Task 3: logging is configured BEFORE data-dir / migration logging in start().

JarvisApp.start() must call _setup_logging() before log_data_dir_choice() and
migrate_from_dotjarvis(), otherwise their INFO lines ("data dir:" and the
migration summary) are emitted with no handler attached and are lost.

We verify the ORDER statically with ast (parsing jarvis/app.py), which needs
neither Qt nor audio deps — so this is not auto-skipped in a lightweight venv
and does not depend on constructing a JarvisApp.

The *content* of those log lines (the "data dir:" string and the 4-number
migration summary) is already covered by test_paths.py and
test_data_migration.py; here we only guard the wiring order.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "jarvis" / "app.py"


def _start_call_order() -> list[str]:
    """Return the sequence of the three relevant calls as they appear in the
    body of JarvisApp.start(), by name."""
    tree = ast.parse(_APP.read_text(encoding="utf-8"), filename=str(_APP))

    # Find the JarvisApp class, then its start() method.
    start_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start":
            start_fn = node
            break
    assert start_fn is not None, "start() not found in app.py"

    watched = {"_setup_logging", "log_data_dir_choice", "migrate_from_dotjarvis"}
    seen: list[str] = []
    for node in ast.walk(start_fn):
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name in watched and name not in seen:
                seen.append(name)
    return seen


def test_setup_logging_runs_before_data_dir_and_migration():
    order = _start_call_order()
    assert "_setup_logging" in order, "start() must configure logging"
    assert "log_data_dir_choice" in order, "start() must announce the data dir"
    assert "migrate_from_dotjarvis" in order, "start() must run migration"

    i_log = order.index("_setup_logging")
    i_dir = order.index("log_data_dir_choice")
    i_mig = order.index("migrate_from_dotjarvis")
    assert i_log < i_dir, "_setup_logging must run before log_data_dir_choice"
    assert i_log < i_mig, "_setup_logging must run before migrate_from_dotjarvis"
