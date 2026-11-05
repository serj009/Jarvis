"""B-P2-6: running a source checkout on the wrong Python says so."""

from __future__ import annotations

import sys

import pytest

import jarvis.paths as paths


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "install_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    return tmp_path


def test_hint_when_the_global_python_runs_a_checkout_with_a_venv(checkout, monkeypatch):
    (checkout / ".venv").mkdir()
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    hint = paths.outside_project_venv()
    assert hint is not None
    assert ".venv" in hint and "-m jarvis" in hint and sys.executable in hint


def test_no_hint_inside_a_virtualenv(checkout, monkeypatch):
    (checkout / ".venv").mkdir()
    monkeypatch.setattr(sys, "prefix", "/some/venv")
    assert paths.outside_project_venv() is None


def test_no_hint_without_a_venv_folder(checkout, monkeypatch):
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    assert paths.outside_project_venv() is None


def test_no_hint_in_a_frozen_build(checkout, monkeypatch):
    (checkout / ".venv").mkdir()
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert paths.outside_project_venv() is None


def test_wake_word_error_carries_the_hint(monkeypatch):
    from jarvis.audio.wake_word import WakeWordModelMissingError

    monkeypatch.setattr(paths, "outside_project_venv", lambda: "Start it from the .venv.")
    assert str(WakeWordModelMissingError("No ONNX model.")) == (
        "No ONNX model. Start it from the .venv."
    )
    monkeypatch.setattr(paths, "outside_project_venv", lambda: None)
    assert str(WakeWordModelMissingError("No ONNX model.")) == "No ONNX model."


def test_environment_errors_are_told_apart():
    from jarvis.app import _is_environment_error

    assert _is_environment_error("WakeWordModelMissingError: No ONNX model")
    assert not _is_environment_error("RuntimeError: device busy")
