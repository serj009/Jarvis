"""Pre-flight Ollama availability check (T1.5).

Called early in ``JarvisApp.start()`` — before the audio stack boots —
to produce a clear, user-friendly diagnostic instead of an opaque
``httpx.ConnectError`` buried inside a traceback.

The check is deliberately lightweight: a single ``GET /api/tags`` with a
short timeout.  If Ollama is not reachable it attempts to auto-start it
(``ollama serve`` or the desktop app on Windows) and retries once.

Returns
-------
OllamaCheckResult
    A dataclass with ``.ok``, ``.warning``, and ``.detail`` so the
    caller can decide whether to abort, warn, or continue.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_CONNECT_TIMEOUT = 3.0  # seconds
_RETRY_DELAY = 4.0  # seconds after auto-start before retry


@dataclass(frozen=True, slots=True)
class OllamaCheckResult:
    """Outcome of the pre-flight Ollama check."""

    ok: bool
    """``True`` if Ollama is reachable (models may or may not be pulled)."""

    warning: str
    """Non-empty user-facing message when *ok* is False."""

    detail: str
    """Technical detail for logs (empty when ok)."""

    auto_started: bool = False
    """``True`` if we successfully launched ``ollama serve``."""


def check_ollama(endpoint: str | None = None) -> OllamaCheckResult:
    """Synchronous pre-flight check.  Safe to call from the main thread.

    Parameters
    ----------
    endpoint:
        Override the Ollama base URL (default ``http://127.0.0.1:11434``).
    """
    url = (endpoint.rstrip("/") + "/api/tags") if endpoint else _OLLAMA_TAGS_URL

    # -- First attempt --
    if _ping(url):
        log.info("ollama pre-flight: reachable")
        return OllamaCheckResult(ok=True, warning="", detail="")

    log.info("ollama pre-flight: not reachable, attempting auto-start...")

    # -- Try to start Ollama --
    started = _try_auto_start()

    if started:
        time.sleep(_RETRY_DELAY)
        if _ping(url):
            log.info("ollama pre-flight: auto-started successfully")
            return OllamaCheckResult(
                ok=True, warning="", detail="auto-started", auto_started=True,
            )

    # -- Still not reachable --
    detail = "Ollama did not respond on GET /api/tags after auto-start attempt."
    warning = (
        "Ollama is not running.\n\n"
        "Please start Ollama manually, or install it from https://ollama.com\n\n"
        "Jarvis will start, but voice commands that require the LLM will "
        "fail until Ollama is available."
    )
    log.warning("ollama pre-flight: %s", detail)
    return OllamaCheckResult(ok=False, warning=warning, detail=detail)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ping(url: str) -> bool:
    """Return True if Ollama responds to GET *url* within timeout."""
    try:
        req = Request(url, method="GET")  # noqa: S310
        with urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:  # noqa: S310
            return resp.status == 200
    except (URLError, OSError, ValueError):
        return False


def _try_auto_start() -> bool:
    """Best-effort attempt to launch the Ollama daemon.

    Strategy:
    1. If ``ollama`` is on PATH → ``ollama serve`` (background).
    2. On Windows, look for the desktop-app exe in common locations.

    Returns True if a process was launched (does NOT mean it's ready yet).
    """
    # Strategy 1: CLI on PATH
    ollama_exe = shutil.which("ollama")
    if ollama_exe:
        return _spawn(ollama_exe, "serve")

    # Strategy 2: Windows desktop app
    if platform.system() == "Windows":
        candidates = [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs", "Ollama", "ollama app.exe",
            ),
            os.path.join(
                os.environ.get("PROGRAMFILES", ""),
                "Ollama", "ollama app.exe",
            ),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return _spawn(path)

    return False


def _spawn(*cmd: str) -> bool:
    """Launch a process detached from the current console."""
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            # CREATE_NO_WINDOW so the serve console doesn't flash.
            kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            )
        subprocess.Popen(list(cmd), **kwargs)  # noqa: S603
        log.info("ollama auto-start: launched %s", cmd)
        return True
    except OSError as exc:
        log.warning("ollama auto-start failed (%s): %s", cmd, exc)
        return False
