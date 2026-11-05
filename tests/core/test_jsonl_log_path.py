"""The structured JSON log lives in the data dir, not the current directory.

B-P2-2: in a source checkout jarvis.log.jsonl was written to the current
directory, so every test run added lines to the same file at the repository
root that live sessions used, and the owner had to tell them apart by the
"data dir: ..." line.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.fixture
def root_handlers():
    """Remove the handlers _setup_logging adds, so other tests are unaffected."""
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def _jsonl_files() -> list[Path]:
    return [
        Path(h.baseFilename)
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and h.baseFilename.endswith(".jsonl")
    ]


def test_jsonl_log_goes_to_the_data_dir(root_handlers, tmp_path, monkeypatch):
    from jarvis.app import _setup_logging
    from jarvis.paths import data_path

    cwd = tmp_path / "checkout"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    _setup_logging("INFO")
    logging.getLogger("jarvis.test").warning("hello")

    assert data_path("logs", "jarvis.log.jsonl") in _jsonl_files()
    assert not (cwd / "jarvis.log.jsonl").exists()
