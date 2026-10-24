"""Tests for jarvis.core.diagnostics (Task C part 1).

Covers crash-handler installation, the asyncio exception handler, the
environment passport, and the privacy invariant that user speech never
reaches the log at INFO or above.

JARVIS_DATA_DIR is repointed to a temp dir by the autouse conftest fixture,
so crash.log / logs land under tmp and the real data dir is never touched.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import threading

import pytest

from jarvis.core import diagnostics as diag


@pytest.fixture(autouse=True)
def _reset_install_state(monkeypatch):
    """Each test starts from a clean, un-installed diagnostics module.

    install_crash_handlers() is global + idempotent, so reset the guard and
    restore the original hooks afterwards to avoid leaking a patched
    sys.excepthook / threading.excepthook into other tests.
    """
    orig_sys = sys.excepthook
    orig_thread = threading.excepthook
    monkeypatch.setattr(diag, "_installed", False)
    monkeypatch.setattr(diag, "_crash_log_fp", None)
    yield
    sys.excepthook = orig_sys
    threading.excepthook = orig_thread


# ---------------------------------------------------------------------------
# crash handlers
# ---------------------------------------------------------------------------


def test_install_is_idempotent_and_sets_hooks():
    diag.install_crash_handlers()
    first_sys = sys.excepthook
    first_thread = threading.excepthook
    assert first_sys is not None
    # Second call must not re-wrap (idempotent).
    diag.install_crash_handlers()
    assert sys.excepthook is first_sys
    assert threading.excepthook is first_thread


def test_faulthandler_file_created():
    """faulthandler is pointed at <data_dir>/logs/crash.log (file created)."""
    from jarvis.paths import data_path

    diag.install_crash_handlers()
    crash = data_path("logs", "crash.log")
    assert crash.exists(), "crash.log should be created for faulthandler"


def test_thread_exception_is_logged(caplog):
    """An uncaught exception in a worker thread lands in the log."""
    diag.install_crash_handlers()

    def boom():
        raise ValueError("thread-boom-sentinel")

    with caplog.at_level(logging.CRITICAL, logger="jarvis.core.diagnostics"):
        t = threading.Thread(target=boom, name="worker-x")
        t.start()
        t.join()

    hits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert hits, "expected a CRITICAL record for the thread crash"
    assert any("worker-x" in r.getMessage() for r in hits)
    assert any(r.exc_info for r in hits), "traceback must be attached"


def test_keyboardinterrupt_in_thread_not_treated_as_crash():
    """A KeyboardInterrupt is delegated to the previous hook, not logged."""
    seen = {"prev": False}

    def fake_prev(args):
        seen["prev"] = True

    # Make fake_prev the hook install_crash_handlers() captures as "previous",
    # so a KeyboardInterrupt should be delegated straight to it.
    threading.excepthook = fake_prev
    diag.install_crash_handlers()

    class Args:
        exc_type = KeyboardInterrupt
        exc_value = KeyboardInterrupt()
        exc_traceback = None
        thread = threading.current_thread()

    threading.excepthook(Args())
    assert seen["prev"] is True


def test_asyncio_exception_handler_logs(caplog):
    async def run():
        loop = asyncio.get_running_loop()
        diag.install_asyncio_exception_handler(loop)
        loop.call_exception_handler(
            {"message": "boom-async", "exception": RuntimeError("async-sentinel")}
        )

    with caplog.at_level(logging.ERROR, logger="jarvis.core.diagnostics"):
        asyncio.run(run())

    hits = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert hits
    assert any("boom-async" in r.getMessage() for r in hits)


# ---------------------------------------------------------------------------
# environment passport
# ---------------------------------------------------------------------------


def test_passport_has_expected_keys(monkeypatch):
    # Avoid spawning nvidia-smi in the test environment.
    monkeypatch.setattr(diag, "nvidia_driver_version", lambda: None)
    p = diag.build_environment_passport(gpu_name="RTX 4070", stt_device="cuda")
    for key in (
        "python", "os", "jarvis", "ctranslate2", "faster_whisper",
        "sounddevice", "torch", "gpu", "driver", "data_dir", "stt_device",
    ):
        assert key in p, f"passport missing {key}"
    assert p["gpu"] == "RTX 4070"
    assert p["stt_device"] == "cuda"
    assert p["driver"] == "-"  # None -> "-"


def test_passport_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(diag, "nvidia_driver_version", lambda: None)
    with caplog.at_level(logging.INFO, logger="jarvis.core.diagnostics"):
        diag.log_environment_passport(gpu_name="-", stt_device="cpu")
    passport_lines = [
        r for r in caplog.records if "env passport:" in r.getMessage()
    ]
    assert len(passport_lines) == 1


def test_driver_version_none_without_nvidia_smi(monkeypatch):
    """nvidia_driver_version returns None when nvidia-smi is absent."""
    # which() is imported lazily inside the function from stdlib shutil.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert diag.nvidia_driver_version() is None


# ---------------------------------------------------------------------------
# privacy: user content must never reach INFO+ logs
# ---------------------------------------------------------------------------


def test_passport_contains_no_user_content(monkeypatch):
    """The passport is versions/devices/paths only — no free-text fields."""
    monkeypatch.setattr(diag, "nvidia_driver_version", lambda: None)
    p = diag.build_environment_passport(gpu_name="-", stt_device="cpu")
    blob = " ".join(str(v) for v in p.values()).lower()
    assert "weather" not in blob
    assert "remind me" not in blob
