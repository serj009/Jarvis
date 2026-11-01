"""Test fixtures for UI tests.

Sets the Qt platform to "offscreen" BEFORE PySide6 is imported anywhere
in the test process so QObject construction works in headless CI without
a real display. The QApplication fixture is session-scoped because Qt
forbids constructing a second QApplication in the same process.

PySide6 is imported LAZILY (inside the fixtures) so that collecting this
directory does not hard-require Qt: a machine without PySide6 (e.g. the
headless .test_venv) still collects tests, and the Qt-dependent fixtures
skip rather than erroring at import time. Pure-Python UI logic lives in
tests/ui_logic/ and never touches this conftest.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _no_qnam_network(request, monkeypatch):
    """Network safety net (Task 9a): any QNetworkAccessManager request in a
    UI test raises, so a widget cannot silently hit the real network (e.g.
    a favicon fetch). Covers get/post/head/sendCustomRequest.

    Opt out with @pytest.mark.allow_qnam for a test that deliberately
    exercises the network path — it must then install its own fake manager
    / reply. favicons default to "off" anyway (no network), so this only
    trips a test that forced direct/google without stubbing.

    No-ops when PySide6 is absent (nothing to patch)."""
    if request.node.get_closest_marker("allow_qnam") is not None:
        return
    try:
        from PySide6.QtNetwork import QNetworkAccessManager
    except ImportError:
        return  # headless env — no Qt network to guard

    def _blocked(*_args, **_kwargs):
        raise AssertionError("Real network access attempted in a UI test")

    for method in ("get", "post", "head", "sendCustomRequest"):
        if hasattr(QNetworkAccessManager, method):
            monkeypatch.setattr(QNetworkAccessManager, method, _blocked)
