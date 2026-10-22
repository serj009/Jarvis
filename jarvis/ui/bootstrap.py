"""Qt bootstrap seam for the composition root.

SPEC.md § Module boundaries: "No file outside ``ui/`` may import from
``PySide6``." ``jarvis/app.py`` is the composition root and lives outside
``ui/``, yet it must create the ``QApplication``, show fatal-error dialogs
before the UI exists, and schedule callbacks onto the Qt loop.

This module is the single ``ui/`` seam that owns those Qt primitives, so
``app.py`` depends on ``jarvis.ui.bootstrap`` instead of ``PySide6``
directly. The boundary stays intact and the Qt dependency is swappable from
one place.

Two shapes are provided:

* Re-exports of the Qt classes app.py uses as values/annotations
  (``QApplication``, ``QMessageBox``, ``QTimer``) — so existing call sites
  (``QTimer.singleShot(...)``, ``QMessageBox.critical(...)``,
  ``cast(QApplication, ...)``) keep working unchanged.
* Thin helpers (``create_qt_app``, ``schedule``, ``show_critical``,
  ``show_warning``) for new code that prefers an intention-revealing API.
"""

from __future__ import annotations

import sys
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

__all__ = [
    "QApplication",
    "QMessageBox",
    "QTimer",
    "create_qt_app",
    "schedule",
    "show_critical",
    "show_warning",
]


def create_qt_app() -> QApplication:
    """Return the process-wide ``QApplication``, creating it if needed.

    ``QApplication.instance()`` is typed as the base ``QCoreApplication``
    singleton; cast to ``QApplication`` because a Jarvis process always runs
    the widget application (either the one created here, or the one a
    pytest-qt fixture created the same way).
    """
    return cast(QApplication, QApplication.instance() or QApplication(sys.argv))


def schedule(delay_ms: int, callback) -> None:
    """Run *callback* on the Qt loop after *delay_ms* milliseconds.

    Wraps ``QTimer.singleShot``. ``delay_ms=0`` defers to the next event-loop
    turn — the standard way to hop back onto the Qt thread.
    """
    QTimer.singleShot(delay_ms, callback)


def show_critical(title: str, text: str, parent=None) -> None:
    """Show a modal critical error dialog (``QMessageBox.critical``)."""
    QMessageBox.critical(parent, title, text)


def show_warning(title: str, text: str, parent=None) -> None:
    """Show a modal warning dialog (``QMessageBox.warning``)."""
    QMessageBox.warning(parent, title, text)
