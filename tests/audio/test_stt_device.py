"""STT CUDA device-resolution tests (Задача 2).

These cover _resolve_device / _detect_ctranslate2_cuda / _has_nvidia_gpu and
the Windows CUDA DLL helper — all WITHOUT a real GPU, ctranslate2, or
faster_whisper (ctranslate2 is injected as a fake module via monkeypatch).

The module-level ctranslate2 / faster_whisper imports in stt.py are lazy, so
importing FasterWhisperSTT here needs neither package.
"""

from __future__ import annotations

import logging
import sys
import types

from jarvis.audio.stt import (
    FasterWhisperSTT,
    _detect_ctranslate2_cuda,
    _has_nvidia_gpu,
)


def _fake_ct2(*, device_count: int, compute_types=("float16",),
             probe_raises: Exception | None = None) -> types.ModuleType:
    """Build a stand-in ctranslate2 module."""
    mod = types.ModuleType("ctranslate2")

    def get_cuda_device_count() -> int:
        return device_count

    def get_supported_compute_types(device: str):
        if probe_raises is not None:
            raise probe_raises
        return set(compute_types)

    mod.get_cuda_device_count = get_cuda_device_count  # type: ignore[attr-defined]
    mod.get_supported_compute_types = get_supported_compute_types  # type: ignore[attr-defined]
    return mod


# --- _detect_ctranslate2_cuda ----------------------------------------


def test_no_cuda_device_reports_cpu_reason(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=0))
    ok, reason = _detect_ctranslate2_cuda()
    assert ok is False
    assert "no CUDA device" in reason


def test_one_device_no_torch_selects_cuda(monkeypatch):
    # torch deliberately absent — ctranslate2 alone must be authoritative.
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=1))
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch -> ImportError
    ok, reason = _detect_ctranslate2_cuda()
    assert ok is True
    assert reason == ""


def test_device_present_but_libs_fail_gives_reason(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ctranslate2",
        _fake_ct2(device_count=1, probe_raises=RuntimeError("cudnn64_9.dll missing")),
    )
    ok, reason = _detect_ctranslate2_cuda()
    assert ok is False
    assert "CUDA libraries failed to load" in reason
    assert "cudnn64_9.dll missing" in reason
    assert "gpu extra" in reason


def test_ctranslate2_absent_reports_reason(monkeypatch):
    # Simulate ctranslate2 not installed.
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    ok, reason = _detect_ctranslate2_cuda()
    assert ok is False
    assert "ctranslate2 not installed" in reason


def test_base_install_without_nvidia_wheels_falls_back_to_cpu(monkeypatch, caplog):
    """Base install (no .[gpu] extra): ctranslate2 sees a device but the CUDA
    runtime DLLs (cuBLAS/cuDNN) are absent, so the backend probe fails. STT
    must resolve to CPU cleanly with a precise, actionable reason — no crash.
    """
    missing = ModuleNotFoundError("No module named 'nvidia'")
    monkeypatch.setitem(
        sys.modules, "ctranslate2",
        _fake_ct2(device_count=1, probe_raises=missing),
    )
    with caplog.at_level(logging.INFO):
        dev = FasterWhisperSTT._resolve_device("auto")
    assert dev == "cpu"  # clean fallback, no exception
    # The reason names the missing-library cause and the fix.
    assert any("CUDA libraries failed to load" in r.message
               and "gpu extra" in r.message
               for r in caplog.records)


# --- _resolve_device end-to-end --------------------------------------


def test_resolve_device_cpu_is_always_cpu(monkeypatch):
    assert FasterWhisperSTT._resolve_device("cpu") == "cpu"


def test_resolve_device_auto_uses_cuda_when_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=1))
    assert FasterWhisperSTT._resolve_device("auto") == "cuda"


def test_resolve_device_configured_cuda_without_gpu_warns(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=0))
    with caplog.at_level(logging.WARNING):
        dev = FasterWhisperSTT._resolve_device("cuda")
    assert dev == "cpu"
    assert any("device='cuda' requested but CUDA is unavailable" in r.message
               for r in caplog.records)


def test_resolve_device_auto_nvidia_present_but_no_cuda_warns(
    monkeypatch, caplog,
):
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=0))
    # Pretend an NVIDIA card is present (nvidia-smi on PATH).
    monkeypatch.setattr("jarvis.audio.stt.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    with caplog.at_level(logging.WARNING):
        dev = FasterWhisperSTT._resolve_device("auto")
    assert dev == "cpu"
    assert any("NVIDIA GPU is present but CUDA is" in r.message
               for r in caplog.records)


def test_resolve_device_auto_no_nvidia_is_info_not_warning(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ct2(device_count=0))
    monkeypatch.setattr("jarvis.audio.stt.shutil.which", lambda name: None)
    with caplog.at_level(logging.INFO):
        dev = FasterWhisperSTT._resolve_device("auto")
    assert dev == "cpu"
    # No WARNING, only INFO.
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert any("no CUDA device" in r.message and r.levelno == logging.INFO
               for r in caplog.records)


# --- _has_nvidia_gpu -------------------------------------------------


def test_has_nvidia_gpu_true_when_smi_present(monkeypatch):
    monkeypatch.setattr("jarvis.audio.stt.shutil.which", lambda n: "/x/nvidia-smi")
    assert _has_nvidia_gpu() is True


def test_has_nvidia_gpu_false_when_absent(monkeypatch):
    monkeypatch.setattr("jarvis.audio.stt.shutil.which", lambda n: None)
    assert _has_nvidia_gpu() is False


# --- CUDA DLL helper (platform) --------------------------------------


def test_add_cuda_dll_directories_noop_off_windows(monkeypatch):
    from jarvis.platform import cuda_dlls

    monkeypatch.setattr(cuda_dlls.sys, "platform", "linux")
    assert cuda_dlls.add_cuda_dll_directories() == []


def test_add_cuda_dll_directories_runs_on_windows(monkeypatch, tmp_path):
    from jarvis.platform import cuda_dlls

    # Pretend Windows with a fake site-packages holding one nvidia DLL dir.
    monkeypatch.setattr(cuda_dlls.sys, "platform", "win32")
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(cuda_dlls, "_site_packages_dirs", lambda: [tmp_path])

    added: list[str] = []
    # os.add_dll_directory only exists on Windows; inject a fake.
    monkeypatch.setattr(
        cuda_dlls.os, "add_dll_directory",
        lambda p: added.append(p), raising=False,
    )
    result = cuda_dlls.add_cuda_dll_directories()
    assert str(bin_dir) in result
    assert str(bin_dir) in added
