r"""Installed-apps index refresh (extracted from JarvisApp, Этап 11 step 1).

Know which programs are installed before the first "open X". The saved copy
(%APPDATA%\Jarvis\installed_apps.json) is loaded synchronously — a small JSON
read — and a full rescan runs in a daemon thread that updates both the
in-memory index and the file.

Every failure is logged and ignored: open_app still works, it just scans on
first use as before.

This was ``JarvisApp._start_app_index_refresh``; moved here verbatim (minus
``self``, which it never used) as the first, lowest-risk step of the app.py
decomposition. ``JarvisApp`` keeps a thin delegating wrapper.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger(__name__)


def start_app_index_refresh() -> None:
    """Prime the installed-apps index from cache, then rescan in a thread."""
    if sys.platform != "win32":
        return

    from jarvis.platform.windows_apps import (
        load_installed_app_index,
        prime_installed_app_index,
        refresh_installed_app_index,
    )
    from jarvis.core.config import default_config_path

    path = default_config_path().parent / "installed_apps.json"
    cached = load_installed_app_index(path)
    if cached:
        prime_installed_app_index(cached)
        log.info("[apps] %d installed apps loaded from %s", len(cached), path)

    def scan() -> None:
        started = time.monotonic()
        try:
            count = refresh_installed_app_index(path)
        except Exception:
            log.warning("[apps] installed-apps scan failed", exc_info=True)
            return
        log.info(
            "[apps] scan done: %d installed apps in %d ms, saved to %s",
            count, int((time.monotonic() - started) * 1000), path,
        )

    threading.Thread(target=scan, name="app-index-scan", daemon=True).start()
