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
    _auto_select_model,
    _detect_ctranslate2_cuda,
    _downloaded_model_sizes,
    _has_nvidia_gpu,
    _resolve_model_id,
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


# --- auto model selection (stt.model == "auto") ----------------------


def _make_whisper_cache(root, *sizes):
    """Create faster-whisper-style cache dirs for the given model sizes."""
    for s in sizes:
        (root / f"models--Systran--faster-whisper-{s}").mkdir(parents=True)


def test_downloaded_model_sizes_detects_cache(tmp_path):
    _make_whisper_cache(tmp_path, "small", "medium")
    assert _downloaded_model_sizes(tmp_path) == {"small", "medium"}


def test_downloaded_model_sizes_empty_when_missing(tmp_path):
    assert _downloaded_model_sizes(tmp_path) == set()
    assert _downloaded_model_sizes(None) == set()


def test_auto_on_cpu_keeps_current(monkeypatch, tmp_path, caplog):
    _make_whisper_cache(tmp_path, "small", "medium")
    with caplog.at_level(logging.INFO):
        chosen = _auto_select_model("small", "cpu", tmp_path)
    assert chosen == "small"  # CPU never upgrades
    assert any("on CPU" in r.message for r in caplog.records)


def test_auto_on_cuda_picks_best_downloaded(monkeypatch, tmp_path):
    _make_whisper_cache(tmp_path, "small", "medium")
    # Plenty of free VRAM.
    monkeypatch.setattr("jarvis.audio.stt._free_vram_mb", lambda: 12000)
    chosen = _auto_select_model("small", "cuda", tmp_path)
    assert chosen == "medium"  # best downloaded that fits


def test_auto_on_cuda_skips_model_that_does_not_fit(monkeypatch, tmp_path):
    _make_whisper_cache(tmp_path, "small", "medium")
    # Only ~1.5 GB free: medium (2.6 GB) won't fit, small (1.2 GB) will.
    monkeypatch.setattr("jarvis.audio.stt._free_vram_mb", lambda: 1500)
    chosen = _auto_select_model("small", "cuda", tmp_path)
    assert chosen == "small"


def test_auto_on_cuda_nothing_better_keeps_current_with_hint(
    monkeypatch, tmp_path, caplog,
):
    # Only the current model is downloaded; no upgrade available.
    _make_whisper_cache(tmp_path, "small")
    monkeypatch.setattr("jarvis.audio.stt._free_vram_mb", lambda: 12000)
    with caplog.at_level(logging.INFO):
        chosen = _auto_select_model("small", "cuda", tmp_path)
    assert chosen == "small"
    assert any("download" in r.message.lower() for r in caplog.records)


def test_explicit_size_is_never_auto(monkeypatch):
    # Sanity: a concrete size bypasses auto-selection entirely (the load()
    # path only calls _auto_select_model when model_size == "auto").
    assert "auto" not in ("small", "medium", "base", "tiny")


# --- Runtime CPU-fallback when CUDA runtime DLLs are missing ----------
# ctranslate2 reports a CUDA device at construction but crashes on the first
# encode() with 'Library cublas64_12.dll is not found' when the gpu extra is
# not installed. transcribe() must reload on CPU once and retry, not crash
# every turn.


import numpy as np  # noqa: E402


def test_is_cuda_runtime_error_matches_cublas():
    err = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    assert FasterWhisperSTT._is_cuda_runtime_error(err) is True


def test_is_cuda_runtime_error_ignores_unrelated():
    assert FasterWhisperSTT._is_cuda_runtime_error(
        ValueError("bad audio shape")
    ) is False


class _CublasCrashModel:
    """Fake whisper model: .transcribe() raises the cuBLAS error once per
    call (simulating a CUDA runtime that never works)."""

    def transcribe(self, audio, **kwargs):
        raise RuntimeError(
            "Library cublas64_12.dll is not found or cannot be loaded"
        )


class _OkModel:
    """Fake whisper model: returns one empty-ish segment successfully."""

    def transcribe(self, audio, **kwargs):
        seg = types.SimpleNamespace(
            text="привет", avg_logprob=-0.2, no_speech_prob=0.1,
        )
        info = types.SimpleNamespace(
            language="ru", language_probability=0.9,
        )
        return iter([seg]), info


async def test_transcribe_reloads_on_cpu_after_cuda_runtime_crash(monkeypatch):
    stt = FasterWhisperSTT(device="auto", language="auto")
    # Simulate a loaded CUDA model that crashes on encode.
    stt._model = _CublasCrashModel()
    stt.is_loaded = True
    stt._active_device = "cuda"
    stt._model_id = "small"

    async def fake_reload() -> bool:
        # Swap in a working CPU model, mirroring the real _reload_on_cpu.
        stt._model = _OkModel()
        stt._active_device = "cpu"
        return True

    monkeypatch.setattr(stt, "_reload_on_cpu", fake_reload)

    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    result = await stt.transcribe(pcm)
    # After the CPU reload + retry, we get real text, not an empty ignore.
    assert result.text == "привет"
    assert stt._active_device == "cpu"


async def test_transcribe_falls_back_even_when_cuda_forced(monkeypatch):
    """Задача A pt 4 (REVISED from Bug-1 behaviour): a broken CUDA runtime must
    degrade to CPU even when the user explicitly set device='cuda' — a working
    CPU STT beats crashing every turn. (Previously we refused the downgrade on
    forced cuda; the task reverses that decision.)"""
    stt = FasterWhisperSTT(device="cuda", language="auto")
    stt._model = _CublasCrashModel()
    stt.is_loaded = True
    stt._active_device = "cuda"
    stt._model_id = "small"

    async def fake_reload() -> bool:
        stt._model = _OkModel()
        stt._active_device = "cpu"
        return True

    monkeypatch.setattr(stt, "_reload_on_cpu", fake_reload)
    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    result = await stt.transcribe(pcm)
    assert result.text == "привет"
    assert stt._active_device == "cpu"


async def test_transcribe_passes_through_unrelated_error(monkeypatch):
    """A non-CUDA error (e.g. bad audio) is NOT treated as a GPU failure: no
    reload, returns empty ignore."""
    class _BadAudioModel:
        def transcribe(self, audio, **kwargs):
            raise ValueError("bad audio shape")

    stt = FasterWhisperSTT(device="auto", language="auto")
    stt._model = _BadAudioModel()
    stt.is_loaded = True
    stt._active_device = "cuda"
    stt._model_id = "small"

    called = False

    async def should_not_reload(reason: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(stt, "_reload_on_cpu_locked", should_not_reload)
    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    result = await stt.transcribe(pcm)
    assert result.text == ""
    assert called is False, "unrelated error must not trigger GPU fallback"


async def test_fallback_happens_once_under_concurrency(monkeypatch):
    """Задача A pt 2: two concurrent transcribe() calls that both hit the CUDA
    error must reload the CPU model only ONCE (serialised by the lock)."""
    import asyncio

    stt = FasterWhisperSTT(device="auto", language="auto")
    stt._model = _CublasCrashModel()
    stt.is_loaded = True
    stt._active_device = "cuda"
    stt._model_id = "small"

    reload_count = 0

    async def counting_reload() -> bool:
        nonlocal reload_count
        reload_count += 1
        await asyncio.sleep(0.01)  # let the second call queue on the lock
        stt._model = _OkModel()
        stt._active_device = "cpu"
        return True

    monkeypatch.setattr(stt, "_reload_on_cpu", counting_reload)
    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    r1, r2 = await asyncio.gather(stt.transcribe(pcm), stt.transcribe(pcm))
    assert reload_count == 1, f"reload must happen once, got {reload_count}"
    assert r1.text == "привет" and r2.text == "привет"


async def test_reload_on_cpu_downgrades_auto_model_to_small(monkeypatch):
    """Задача A pt 3: an auto-selected model reverts to 'small' on CPU reload
    (a large auto pick would be unusably slow on CPU)."""
    stt = FasterWhisperSTT(model_size="auto", device="auto", language="en")
    stt._model_id = "medium"  # auto had picked a large model on GPU
    stt._active_device = "cuda"

    built: list = []

    class _FakeWM:
        def __init__(self, model_id, **kwargs):
            built.append((model_id, kwargs.get("device")))

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _FakeWM
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    ok = await stt._reload_on_cpu()
    assert ok is True
    # "small" resolves to a language-specific id (e.g. small.en for English),
    # so compare against the real resolver output, not a bare "small".
    expected_small = _resolve_model_id("small", "en")
    assert built == [(expected_small, "cpu")], (
        f"auto must downgrade to small ({expected_small}), got {built}"
    )
    assert stt._model_id == expected_small


async def test_reload_on_cpu_keeps_explicit_model(monkeypatch):
    """Задача A pt 3: an explicitly configured model is NOT downgraded."""
    stt = FasterWhisperSTT(model_size="medium", device="auto", language="en")
    stt._model_id = "medium"
    stt._active_device = "cuda"

    built: list = []

    class _FakeWM:
        def __init__(self, model_id, **kwargs):
            built.append((model_id, kwargs.get("device")))

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _FakeWM
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    ok = await stt._reload_on_cpu()
    assert ok is True
    assert built == [("medium", "cpu")], f"explicit model must be kept, got {built}"


async def test_gpu_fallback_notifies_bus_once():
    """Задача A pt 5: a one-shot NonFatalError(module='stt', issue='gpu_fallback')
    is published, and only once per session."""
    published: list = []

    class _Bus:
        def publish(self, evt):
            published.append(evt)

    stt = FasterWhisperSTT(device="auto", language="en", bus=_Bus())
    stt._notify_gpu_fallback("cublas missing")
    stt._notify_gpu_fallback("cublas missing")  # second call is a no-op
    assert len(published) == 1
    evt = published[0]
    assert evt.module == "stt"
    assert evt.issue == "gpu_fallback"
    assert evt.expected == "cuda" and evt.actual == "cpu"
