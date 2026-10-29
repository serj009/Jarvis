"""Tests for T12.3 — Auto-restart loop in jarvis/__main__.py."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from jarvis.__main__ import MAX_RESTARTS, main


@pytest.fixture(autouse=True)
def _mock_jarvis_app(monkeypatch):
    """Inject a fake jarvis.app module so main() never imports the real one.
    
    The real jarvis.app pulls in PySide6, sounddevice, and dozens of heavy
    deps that aren't available in the lightweight test venv. We plant a
    fake module in sys.modules BEFORE main() runs 'from jarvis.app import run'.
    """
    fake_app = types.ModuleType("jarvis.app")
    fake_app.run = MagicMock(return_value=0)
    monkeypatch.setitem(sys.modules, "jarvis.app", fake_app)
    return fake_app


class TestRestartLoop:
    """Verify the restart loop honours exit codes and limits."""

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__.log")
    def test_clean_exit_no_restart(self, _log, _sleep, _mock_jarvis_app) -> None:
        """exit_code=0 → SystemExit(0), no restart attempts."""
        _mock_jarvis_app.run = MagicMock(return_value=0)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        assert _mock_jarvis_app.run.call_count == 1

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__.log")
    def test_crash_restarts_up_to_max(self, _log, _sleep, _mock_jarvis_app) -> None:
        """Non-zero exit → restart up to MAX_RESTARTS then give up."""
        _mock_jarvis_app.run = MagicMock(return_value=1)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert _mock_jarvis_app.run.call_count == MAX_RESTARTS

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__._write_crash_log")
    @patch("jarvis.__main__.log")
    def test_exception_restarts_up_to_max(self, _log, _crash_log, _sleep, _mock_jarvis_app) -> None:
        """Unhandled exception → restart up to MAX_RESTARTS then re-raise."""
        _mock_jarvis_app.run = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            main()

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__.log")
    def test_keyboard_interrupt_stops_immediately(self, _log, _sleep, _mock_jarvis_app) -> None:
        """Ctrl+C (KeyboardInterrupt) must exit without restart."""
        _mock_jarvis_app.run = MagicMock(side_effect=KeyboardInterrupt)
        main()  # must return cleanly

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__.log")
    def test_crash_then_clean_exit(self, _log, _sleep, _mock_jarvis_app) -> None:
        """One crash then a clean exit → total 2 calls, exits cleanly."""
        call_count = 0

        def _side_effect():
            nonlocal call_count
            call_count += 1
            return 1 if call_count == 1 else 0

        _mock_jarvis_app.run = MagicMock(side_effect=_side_effect)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        assert call_count == 2

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__._write_crash_log")
    @patch("jarvis.__main__.log")
    def test_exception_then_clean_exit(self, _log, _crash_log, _sleep, _mock_jarvis_app) -> None:
        """One exception then a clean exit → recovered successfully."""
        call_count = 0

        def _side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient")
            return 0

        _mock_jarvis_app.run = MagicMock(side_effect=_side_effect)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        assert call_count == 2

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__.log")
    def test_cooldown_sleep_called(self, _log, mock_sleep, _mock_jarvis_app) -> None:
        """Verify RESTART_COOLDOWN sleep is called between restarts."""
        _mock_jarvis_app.run = MagicMock(return_value=1)
        with pytest.raises(SystemExit):
            main()
        # sleep called before each retry (MAX_RESTARTS - 1 retries + 1 final)
        assert mock_sleep.call_count >= 1

    @patch("jarvis.__main__.time.sleep", return_value=None)
    @patch("jarvis.__main__._write_crash_log")
    @patch("jarvis.__main__.log")
    def test_crash_log_written_on_exception(
        self, _log, mock_crash_log, _sleep, _mock_jarvis_app
    ) -> None:
        """Unhandled exception must write a crash log."""
        _mock_jarvis_app.run = MagicMock(side_effect=RuntimeError("oops"))
        with pytest.raises(RuntimeError):
            main()
        assert mock_crash_log.call_count >= 1
