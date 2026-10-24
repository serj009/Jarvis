"""Task C §1: rotating file log is installed in every run mode.

Tests target jarvis.core.diagnostics.install_rotating_file_log() directly so
they do NOT import jarvis.app (which pulls in sounddevice / PySide6 and would
be auto-skipped in a lightweight venv). This keeps the rotation contract
testable in .test_venv.

Windows file-lock safety: every test removes the handler from the root logger
and calls handler.close() in a fixture, so tmp_path cleanup never hits a held
file (WinError 5).

The autouse conftest fixture repoints JARVIS_DATA_DIR to a temp dir, so the
log lands under tmp.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

import pytest

from jarvis.core import diagnostics as diag


@pytest.fixture
def clean_root():
    """Yield the root logger; strip + close any handlers a test adds."""
    root = logging.getLogger()
    before = list(root.handlers)
    saved_level = root.level
    yield root
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    root.setLevel(saved_level)


def _rotating_for(path_str: str) -> list[RotatingFileHandler]:
    target = os.path.abspath(path_str)
    return [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", None) == target
    ]


def test_handler_installed_with_expected_rotation_params(clean_root):
    from jarvis.paths import data_path

    log_path = data_path("logs", "jarvis.log")
    handler = diag.install_rotating_file_log()
    assert handler is not None
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 5
    assert log_path.parent.is_dir()  # create=True made the logs dir


def test_handler_not_duplicated(clean_root):
    from jarvis.paths import data_path

    log_path = data_path("logs", "jarvis.log")
    h1 = diag.install_rotating_file_log()
    h2 = diag.install_rotating_file_log()
    assert h1 is h2, "second call must return the same handler, not add one"
    assert len(_rotating_for(str(log_path))) == 1


def test_rotation_really_rotates(clean_root):
    """Small maxBytes + lots of writes → jarvis.log.1 appears, capped at
    backupCount. Proves rollover works, not just that params are set."""
    from jarvis.paths import data_path

    log_path = data_path("logs", "jarvis.log")

    handler = diag.install_rotating_file_log(
        formatter=logging.Formatter("%(message)s")
    )
    assert handler is not None
    handler.maxBytes = 1024  # 1 KB
    handler.backupCount = 2
    clean_root.setLevel(logging.INFO)

    for i in range(200):
        logging.getLogger().info("x" * 60 + f" line {i}")
    handler.flush()

    names = sorted(p.name for p in log_path.parent.iterdir())
    rotated = [n for n in names if n.startswith("jarvis.log.")]
    assert "jarvis.log.1" in rotated, f"expected rotation, got {names}"
    assert len(rotated) <= 2, f"must not exceed backupCount=2, got {rotated}"


def test_quiet_handler_swallows_write_errors(clean_root, capsys):
    """A locked/broken log file must not raise into the caller; warn once.

    Simulate a busy file on Windows: the stream's write() raises on every
    record. The stdlib emit() catches it and routes to handleError, which our
    subclass keeps quiet — even with the dev default logging.raiseExceptions
    = True.
    """
    handler = diag.install_rotating_file_log(
        formatter=logging.Formatter("%(message)s")
    )
    assert handler is not None

    class _BrokenStream:
        def write(self, *a):
            raise PermissionError("file busy")

        def flush(self):
            pass

        def close(self):
            pass

    handler.stream = _BrokenStream()  # type: ignore[assignment]

    rec = logging.LogRecord("t", logging.ERROR, "x.py", 1, "msg", None, None)
    try:
        handler.handle(rec)
        handler.handle(rec)
    except Exception as exc:  # pragma: no cover - the bug we prevent
        pytest.fail(f"handler let an exception escape: {exc!r}")

    err = capsys.readouterr().err
    assert err.count("log file is busy") <= 1
