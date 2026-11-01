"""Tests for the UI network safety net (Task 9a).

The autouse _no_qnam_network fixture (tests/ui/conftest.py) makes any
QNetworkAccessManager request raise, unless a test opts out with
@pytest.mark.allow_qnam. Requires PySide6 — skipped in the headless
.test_venv.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtNetwork")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest  # noqa: E402


def test_qnam_get_blocked_without_marker(qapp):
    """Without @allow_qnam the safety net turns .get() into an AssertionError."""
    manager = QNetworkAccessManager()
    with pytest.raises(AssertionError, match="Real network access"):
        manager.get(QNetworkRequest(QUrl("https://example.com/favicon.ico")))


@pytest.mark.allow_qnam
def test_qnam_allowed_with_marker_and_stub(qapp, monkeypatch):
    """With @allow_qnam the net is NOT patched, and a test may stub the
    manager itself (here we stub .get to return a sentinel, proving the
    real method is reachable — no AssertionError)."""
    manager = QNetworkAccessManager()
    sentinel = object()
    monkeypatch.setattr(
        QNetworkAccessManager, "get", lambda self, req: sentinel
    )
    result = manager.get(QNetworkRequest(QUrl("https://example.com/favicon.ico")))
    assert result is sentinel
