"""The structured JSON log lives in the data dir, once.

B-P2-2: in a source checkout jarvis.log.jsonl was written to the current
directory, so every test run added lines to the same file at the repository
root that live sessions used, and the owner had to tell them apart by the
"data dir: ..." line.

Also: start() calls _setup_logging twice, and each call added another JSONL
handler, so every line was in the file twice.

These tests call _install_jsonl_log() only, not the whole _setup_logging():
that one restyles every root handler (pytest's included) and the process
then intermittently crashed on exit.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest


@pytest.fixture
def jsonl_handlers():
    """Collect the handlers the test installs; remove and close them after."""
    root = logging.getLogger()
    installed: list[logging.Handler] = []
    yield installed
    for handler in installed:
        if handler in root.handlers:
            root.removeHandler(handler)
        handler.close()


def _jsonl_files() -> list[Path]:
    return [
        Path(h.baseFilename)
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and h.baseFilename.endswith(".jsonl")
    ]


def test_jsonl_log_goes_to_the_data_dir(jsonl_handlers, tmp_path, monkeypatch):
    from jarvis.app import _install_jsonl_log
    from jarvis.paths import data_path

    cwd = tmp_path / "checkout"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    handler = _install_jsonl_log()
    assert handler is not None
    jsonl_handlers.append(handler)

    assert _jsonl_files() == [data_path("logs", "jarvis.log.jsonl")]
    assert not (cwd / "jarvis.log.jsonl").exists()


def test_installing_twice_writes_each_line_once(jsonl_handlers):
    from jarvis.app import _install_jsonl_log
    from jarvis.paths import data_path

    first = _install_jsonl_log()
    second = _install_jsonl_log()
    assert first is not None
    jsonl_handlers.append(first)
    assert second is first
    assert _jsonl_files() == [data_path("logs", "jarvis.log.jsonl")]

    logging.getLogger("jarvis.test").warning("only once, please")
    first.flush()
    lines = data_path("logs", "jarvis.log.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum("only once, please" in line for line in lines) == 1


def test_setup_logging_installs_the_jsonl_log():
    from jarvis import app

    assert "_install_jsonl_log()" in inspect.getsource(app._setup_logging)
