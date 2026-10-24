"""Task C §2: CLI argument parsing + entry point in jarvis.__main__.

jarvis.__main__ only imports stdlib at module top (argparse/logging/time/
traceback/pathlib); the heavy jarvis.app / jarvis.core.doctor imports happen
lazily inside _cli()/main(). So importing _parse_args / _cli here does not pull
in audio/Qt and this file is not auto-skipped in a lightweight venv.

The argument parsing lives in _cli() (the real entry point), NOT in main(), so
main() stays a plain no-arg function the restart-loop tests call directly
without argparse consuming pytest's own argv.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.__main__ import _cli, _parse_args


def test_no_args_means_launch_normally():
    ns = _parse_args([])
    assert ns.doctor is False
    assert ns.json is False
    assert ns.redact is False
    assert ns.deep is False


def test_doctor_flag():
    ns = _parse_args(["--doctor"])
    assert ns.doctor is True


def test_doctor_with_modifiers():
    ns = _parse_args(["--doctor", "--json", "--redact", "--deep"])
    assert ns.doctor and ns.json and ns.redact and ns.deep


def test_cli_doctor_exits_without_launching():
    """_cli(--doctor) runs the doctor and raises SystemExit; main() (the
    assistant launch) must NOT be called."""
    with (
        patch("jarvis.__main__.main") as mock_main,
        patch("jarvis.core.doctor.run_doctor", return_value=0) as mock_doctor,
    ):
        with pytest.raises(SystemExit) as exc:
            _cli(["--doctor"])
    assert exc.value.code == 0
    mock_doctor.assert_called_once()
    mock_main.assert_not_called()


def test_cli_no_args_launches_main():
    """_cli() with no diagnostics flag delegates to main()."""
    with patch("jarvis.__main__.main") as mock_main:
        _cli([])
    mock_main.assert_called_once()
