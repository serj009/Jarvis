"""Crash capture + environment passport (Task C, part 1).

Two responsibilities, both best-effort and side-effect-light so they are
safe to wire into the boot path:

1. :func:`install_crash_handlers` — route every *uncaught* error to the log
   with a full traceback, from any of the four places Python can drop one:

   * ``sys.excepthook``            — main-thread uncaught exceptions
   * ``threading.excepthook``      — exceptions in worker threads (the audio
     thread is the one that matters here)
   * asyncio loop exception handler — "Task exception was never retrieved"
   * ``faulthandler``              — hard crashes (segfault / access
     violation) dumped to ``logs/crash.log``

   A clean ``KeyboardInterrupt`` (Ctrl+C) is NOT treated as a crash — it is
   re-delegated to the previous hook so the normal shutdown path runs.

2. :func:`log_environment_passport` — emit a single INFO line describing the
   runtime (Python / Jarvis / key package versions, GPU + driver, data dir,
   active STT device) so a user's log is enough to diagnose remotely.

Privacy: nothing here logs user speech, LLM replies, or tool results. The
passport only contains versions, device names, and paths.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import subprocess
import sys
import threading
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from typing import Any

log = logging.getLogger(__name__)

# Keep a reference to the crash.log file object for the lifetime of the
# process: faulthandler writes to this fd when the interpreter crashes, so it
# must stay open. Module-level so it is never garbage-collected.
_crash_log_fp: Any = None

# Guard against double installation (idempotent boot wiring).
_installed = False


# ---------------------------------------------------------------------------
# Part 1: crash handlers
# ---------------------------------------------------------------------------


def _crash_log_path() -> Any:
    """``<data_dir>/logs/crash.log`` via the single data-dir resolver."""
    from jarvis.paths import data_path

    return data_path("logs", "crash.log", create=True)


def install_crash_handlers() -> None:
    """Install sys/threading/asyncio hooks + faulthandler. Idempotent.

    Safe to call once early in boot. Never raises — a diagnostics failure
    must not prevent Jarvis from starting.
    """
    global _installed, _crash_log_fp
    if _installed:
        return

    # -- faulthandler: dump native crashes (segfault / access violation) ----
    try:
        path = _crash_log_path()
        # Line-buffered, append: keep prior crashes, flush promptly.
        _crash_log_fp = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        faulthandler.enable(file=_crash_log_fp, all_threads=True)
    except Exception:
        log.debug("faulthandler setup failed", exc_info=True)

    # -- sys.excepthook: main-thread uncaught exceptions --------------------
    _prev_excepthook = sys.excepthook

    def _hook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        # Ctrl+C is a clean shutdown, not a crash: defer to the default.
        if issubclass(exc_type, KeyboardInterrupt):
            _prev_excepthook(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook

    # -- threading.excepthook: worker-thread uncaught exceptions ------------
    _prev_threadhook = threading.excepthook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not None and issubclass(
            args.exc_type, KeyboardInterrupt
        ):
            _prev_threadhook(args)
            return
        log.critical(
            "uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook

    _installed = True
    log.debug("crash handlers installed")


def install_asyncio_exception_handler(loop: Any) -> None:
    """Route unhandled asyncio task exceptions into the log.

    Called with the running loop once it exists. Never raises.
    """
    try:

        def _handler(loop: Any, context: dict[str, Any]) -> None:
            exc = context.get("exception")
            msg = context.get("message", "asyncio error")
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                return
            if exc is not None:
                log.error(
                    "unhandled asyncio exception: %s",
                    msg,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            else:
                log.error("unhandled asyncio error: %s", msg)

        loop.set_exception_handler(_handler)
    except Exception:
        log.debug("asyncio exception handler setup failed", exc_info=True)


# ---------------------------------------------------------------------------
# Part 1 §1: rotating file log (installed in EVERY run mode)
# ---------------------------------------------------------------------------


class _QuietRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler that never lets a write/rollover error escape.

    On Windows a rollover can fail with PermissionError when another process
    (or a second Jarvis instance) holds the log file open. The stdlib handler
    would, with ``logging.raiseExceptions`` True (the default in dev), dump a
    traceback to stderr for *every* record. We instead emit a single warning
    to stderr and then stay silent, so a busy log file can never crash the
    app or flood the console.
    """

    _warned: bool = False

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        if not self._warned:
            self._warned = True
            try:
                sys.stderr.write(
                    "jarvis: log file is busy; file logging degraded "
                    "(further errors suppressed)\n"
                )
            except Exception:
                pass
        # Swallow everything else — no traceback, no re-raise.


def install_rotating_file_log(
    *,
    level: int | None = None,
    formatter: logging.Formatter | None = None,
    correlation_filter: logging.Filter | None = None,
) -> RotatingFileHandler | None:
    """Attach a rotating ``<data_dir>/logs/jarvis.log`` handler to the root.

    5 MB x 5 files, UTF-8. Idempotent: a second call with the same resolved
    path is a no-op (guarded by ``baseFilename``) so the twice-called
    ``_setup_logging`` (INFO then configured level) never stacks handlers.

    Returns the handler (new or pre-existing), or None if setup failed. Never
    raises — a logging failure must not stop boot.
    """
    try:
        from jarvis.paths import data_path

        log_path = data_path("logs", "jarvis.log", create=True)
        resolved = os.path.abspath(str(log_path))

        root = logging.getLogger()
        for h in root.handlers:
            if (
                isinstance(h, RotatingFileHandler)
                and getattr(h, "baseFilename", None) == resolved
            ):
                return h  # already installed — do not duplicate

        handler = _QuietRotatingFileHandler(
            resolved,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        if formatter is not None:
            handler.setFormatter(formatter)
        if correlation_filter is not None:
            handler.addFilter(correlation_filter)
        if level is not None:
            handler.setLevel(level)
        root.addHandler(handler)
        return handler
    except Exception:
        log.debug("rotating file log setup failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Part 1 §3: environment passport
# ---------------------------------------------------------------------------


def _pkg_version(name: str) -> str:
    """Return an installed package version, or ``"-"`` if absent."""
    try:
        return version(name)
    except (PackageNotFoundError, OSError, ValueError):
        return "-"


def _jarvis_version() -> str:
    try:
        return version("jarvis")
    except (PackageNotFoundError, OSError, ValueError):
        return "dev"


def _torch_present() -> bool:
    import importlib.util

    return importlib.util.find_spec("torch") is not None


def nvidia_driver_version() -> str | None:
    """Best-effort GPU driver version via ``nvidia-smi``.

    Read-only: queries driver_version only, no state change. Returns None
    when nvidia-smi is unavailable or fails. Kept separate from
    VRAMManager.snapshot() (which intentionally does not carry the driver
    version) so the manager and its tests are untouched.
    """
    import shutil

    if shutil.which("nvidia-smi") is None:
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip().split("\n")[0].strip()
        return line or None
    except Exception:
        log.debug("nvidia-smi driver_version query failed", exc_info=True)
        return None


def build_environment_passport(
    *, gpu_name: str | None = None, stt_device: str | None = None
) -> dict[str, Any]:
    """Collect version/device facts for the boot passport.

    Pure data gathering — no user content. ``gpu_name`` / ``stt_device`` are
    passed in by the caller when known (from VRAMManager / the STT engine);
    the driver version is probed here.
    """
    from jarvis.paths import user_data_dir

    try:
        data_dir = str(user_data_dir())
    except OSError:
        data_dir = "?"

    return {
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "jarvis": _jarvis_version(),
        "ctranslate2": _pkg_version("ctranslate2"),
        "faster_whisper": _pkg_version("faster-whisper"),
        "sounddevice": _pkg_version("sounddevice"),
        "torch": _pkg_version("torch") if _torch_present() else "-",
        "gpu": gpu_name or "-",
        "driver": nvidia_driver_version() or "-",
        "data_dir": data_dir,
        "stt_device": stt_device or "-",
    }


def log_environment_passport(
    *, gpu_name: str | None = None, stt_device: str | None = None
) -> dict[str, Any]:
    """Emit a single INFO passport line and return the collected dict."""
    passport = build_environment_passport(gpu_name=gpu_name, stt_device=stt_device)
    log.info(
        "env passport: python=%(python)s os=%(os)s jarvis=%(jarvis)s "
        "ctranslate2=%(ctranslate2)s faster_whisper=%(faster_whisper)s "
        "sounddevice=%(sounddevice)s torch=%(torch)s gpu=%(gpu)s "
        "driver=%(driver)s data_dir=%(data_dir)s stt_device=%(stt_device)s",
        passport,
    )
    return passport
