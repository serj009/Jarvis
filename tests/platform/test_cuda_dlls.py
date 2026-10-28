"""Task 1: jarvis.platform.cuda_dlls — PATH registration + idempotency.

Pure stdlib + monkeypatch; no real NVIDIA wheels needed (a fake
site-packages/nvidia/<pkg>/bin tree is built under tmp_path). The autouse
``_reset_cuda_dll_cache`` fixture in tests/conftest.py clears the module cache
and snapshots PATH around each test, so these are order-independent.
"""

from __future__ import annotations

import os

from jarvis.platform import cuda_dlls


def _fake_nvidia(tmp_path, *pkgs: str):
    """Build tmp_path/nvidia/<pkg>/bin for each pkg; return the tmp_path."""
    for pkg in pkgs:
        (tmp_path / "nvidia" / pkg / "bin").mkdir(parents=True)
    return tmp_path


def _win(monkeypatch, tmp_path):
    monkeypatch.setattr(cuda_dlls.sys, "platform", "win32")
    monkeypatch.setattr(cuda_dlls, "_site_packages_dirs", lambda: [tmp_path])
    added: list[str] = []
    monkeypatch.setattr(
        cuda_dlls.os, "add_dll_directory",
        lambda p: added.append(p), raising=False,
    )
    return added


def test_prepends_bin_dirs_to_path(monkeypatch, tmp_path):
    _fake_nvidia(tmp_path, "cublas", "cudnn", "cuda_nvrtc")
    _win(monkeypatch, tmp_path)

    result = cuda_dlls.add_cuda_dll_directories()

    assert result, "should register the nvidia bin dirs"
    path_parts = os.environ["PATH"].split(os.pathsep)
    for d in result:
        assert d in path_parts, f"{d} must be on PATH"
    # All registered dirs occupy the FRONT of PATH. Each prepend pushes to the
    # head, so their relative order is reversed vs the add order — only the
    # "they lead PATH" invariant matters.
    front = path_parts[: len(result)]
    assert set(front) == set(result), f"registered dirs must lead PATH: {front}"


def test_path_changed_once_no_duplicate_on_second_call(monkeypatch, tmp_path, caplog):
    import logging

    _fake_nvidia(tmp_path, "cublas", "cudnn")
    _win(monkeypatch, tmp_path)

    with caplog.at_level(logging.INFO, logger="jarvis.platform.cuda_dlls"):
        first = cuda_dlls.add_cuda_dll_directories()
    path_after_first = os.environ["PATH"]
    info_after_first = [r for r in caplog.records if r.levelno == logging.INFO]

    # Second call: cached → no PATH change, no additional log line.
    with caplog.at_level(logging.INFO, logger="jarvis.platform.cuda_dlls"):
        second = cuda_dlls.add_cuda_dll_directories()

    assert second == first
    assert os.environ["PATH"] == path_after_first, "PATH must not change twice"
    info_after_second = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_after_second) == len(info_after_first), "must not log twice"


def test_no_duplicate_if_already_on_path(monkeypatch, tmp_path):
    tmp_path = _fake_nvidia(tmp_path, "cublas")
    _win(monkeypatch, tmp_path)
    bin_dir = str(tmp_path / "nvidia" / "cublas" / "bin")
    # Pre-seed PATH with the dir already present.
    monkeypatch.setenv("PATH", os.pathsep.join([bin_dir, r"C:\Windows"]))

    cuda_dlls.add_cuda_dll_directories()

    parts = os.environ["PATH"].split(os.pathsep)
    assert parts.count(bin_dir) == 1, "dir must not be duplicated on PATH"


def test_empty_result_logs_none_found(monkeypatch, tmp_path, caplog):
    import logging

    # Windows, but site-packages has no nvidia/ tree.
    monkeypatch.setattr(cuda_dlls.sys, "platform", "win32")
    monkeypatch.setattr(cuda_dlls, "_site_packages_dirs", lambda: [tmp_path])
    monkeypatch.setattr(
        cuda_dlls.os, "add_dll_directory", lambda p: None, raising=False
    )
    path_before = os.environ["PATH"]

    with caplog.at_level(logging.INFO, logger="jarvis.platform.cuda_dlls"):
        result = cuda_dlls.add_cuda_dll_directories()

    assert result == []
    assert os.environ["PATH"] == path_before, "PATH untouched when nothing found"
    assert any("none found" in r.getMessage().lower() for r in caplog.records)
    assert any("looked in" in r.getMessage().lower() for r in caplog.records)


def test_noop_off_windows_leaves_path_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(cuda_dlls.sys, "platform", "linux")
    path_before = os.environ["PATH"]
    assert cuda_dlls.add_cuda_dll_directories() == []
    assert os.environ["PATH"] == path_before


def test_path_restored_after_test_via_fixture(monkeypatch, tmp_path):
    """Sanity: the dir added here must NOT leak — the autouse fixture snapshots
    PATH, so the next test sees the original. We assert the dir is present now;
    its absence later is guaranteed by the fixture (and covered by the off-
    windows test running clean)."""
    _fake_nvidia(tmp_path, "cublas")
    _win(monkeypatch, tmp_path)
    result = cuda_dlls.add_cuda_dll_directories()
    assert result
    assert result[0] in os.environ["PATH"].split(os.pathsep)
