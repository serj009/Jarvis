"""Project-wide pytest configuration.

Keeps test infra concerns (temp-dir safety, global fixtures) out of
individual test modules and pyproject.toml addopts.
"""

from __future__ import annotations

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
