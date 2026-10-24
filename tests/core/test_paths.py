"""Tests for jarvis.paths."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jarvis import paths
from jarvis.paths import install_root, is_frozen


def test_install_root_is_directory():
    root = install_root()
    assert root.is_dir()


def test_is_frozen_false_in_dev():
    assert is_frozen() is False


def test_bundled_asset_report_keys():
    from jarvis.paths import bundled_asset_report

    report = bundled_asset_report()
    assert "voices_dir" in report
    assert "whisper_root" in report


# ---------------------------------------------------------------------------
# user_data_dir() — resolution order (Task 4+B)
# ---------------------------------------------------------------------------


def test_user_data_dir_env_var_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """JARVIS_DATA_DIR takes priority over everything, on any platform."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "explicit"))
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    assert paths.user_data_dir() == tmp_path / "explicit"


def test_user_data_dir_windows_uses_appdata(
    monkeypatch: pytest.MonkeyPatch,
):
    """On Windows with APPDATA set, root is %APPDATA%\\Jarvis."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    result = paths.user_data_dir()
    assert result == Path(r"C:\Users\test\AppData\Roaming") / "Jarvis"


def test_user_data_dir_windows_fallback_without_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """On Windows with APPDATA unset, fall back to ~/AppData/Roaming/Jarvis."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = paths.user_data_dir()
    assert result == tmp_path / "AppData" / "Roaming" / "Jarvis"


def test_user_data_dir_non_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """On non-Windows, root is the historical ~/.jarvis."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.user_data_dir() == tmp_path / ".jarvis"


# ---------------------------------------------------------------------------
# data_path() — layout + lazy creation
# ---------------------------------------------------------------------------


def test_data_path_no_parts_is_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert paths.data_path() == tmp_path


def test_data_path_joins_parts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert paths.data_path("memory", "memory.db") == tmp_path / "memory" / "memory.db"


def test_data_path_create_file_makes_parent_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A file target (has a suffix) creates its parent dir, not the file."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    target = paths.data_path("logs", "jarvis.log", create=True)
    assert target == tmp_path / "logs" / "jarvis.log"
    assert target.parent.is_dir()
    assert not target.exists()


def test_data_path_create_dir_makes_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A directory target (no suffix) is created itself."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    target = paths.data_path("conversations", create=True)
    assert target == tmp_path / "conversations"
    assert target.is_dir()


def test_data_path_no_create_does_not_touch_fs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    target = paths.data_path("embeddings", create=False)
    assert not target.exists()


# ---------------------------------------------------------------------------
# log_data_dir_choice() — one INFO line + temp-dir WARNING rules
# ---------------------------------------------------------------------------


def test_log_data_dir_choice_logs_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Emits a single INFO line naming the dir and the source rule."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with caplog.at_level(logging.INFO, logger="jarvis.paths"):
        result = paths.log_data_dir_choice()
    assert result == tmp_path
    info_lines = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "data dir:" in r.message
    ]
    assert len(info_lines) == 1
    assert "JARVIS_DATA_DIR" in info_lines[0].message


def test_log_data_dir_choice_no_temp_warning_with_env(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A temp-dir root does NOT warn when JARVIS_DATA_DIR is what selected it."""
    import tempfile

    temp_root = Path(tempfile.gettempdir()) / "jarvis_env_root"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(temp_root))
    with caplog.at_level(logging.WARNING, logger="jarvis.paths"):
        paths.log_data_dir_choice()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_log_data_dir_choice_no_temp_warning_under_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Even a bare temp root is silent while running under pytest.

    We force the resolver down the non-env branch (delenv) and point it at a
    temp-dir location; _running_under_pytest() is True in this process, so no
    WARNING must be emitted.
    """
    import tempfile

    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    temp_home = Path(tempfile.gettempdir())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: temp_home))
    with caplog.at_level(logging.WARNING, logger="jarvis.paths"):
        paths.log_data_dir_choice()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_log_data_dir_choice_warns_on_temp_when_not_pytest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A bare temp root DOES warn when not under pytest and env unset."""
    import tempfile

    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    temp_home = Path(tempfile.gettempdir())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: temp_home))
    monkeypatch.setattr(paths, "_running_under_pytest", lambda: False)
    with caplog.at_level(logging.WARNING, logger="jarvis.paths"):
        paths.log_data_dir_choice()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "память не сохранится" in warnings[0].message
