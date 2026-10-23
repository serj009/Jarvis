r"""Make pip-installed NVIDIA CUDA DLLs importable on Windows.

The ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` wheels drop their DLLs
under ``site-packages/nvidia/<pkg>/bin`` rather than on the system PATH.
ctranslate2 (and therefore faster-whisper) dlopen these at import time, so
on Windows they must be registered with ``os.add_dll_directory`` BEFORE
ctranslate2 is imported, or CUDA silently falls back to CPU.

This helper is Windows-only and import-safe everywhere: on non-Windows it is
a no-op (Linux resolves the libs through the wheels' RPATH / the dynamic
loader). It uses only the standard library — no win32, no ctypes.windll — so
it does not trip ``tests/test_architecture_rules.py`` (which gates win32
imports to ``platform/windows.py``).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# NVIDIA wheel sub-packages whose bin/ dirs hold the CUDA runtime DLLs that
# ctranslate2 needs for the CUDA backend.
_NVIDIA_DLL_PACKAGES = ("cublas", "cudnn", "cuda_runtime")


def add_cuda_dll_directories() -> list[str]:
    """Register NVIDIA wheel DLL directories for the current process.

    Returns the list of directories actually added (empty on non-Windows or
    when no NVIDIA wheels are installed). Never raises — a failure to add a
    directory is logged at debug and skipped, leaving the CPU path intact.
    """
    if sys.platform != "win32":
        return []
    if not hasattr(os, "add_dll_directory"):  # pragma: no cover - win-only API
        return []

    added: list[str] = []
    for site_dir in _site_packages_dirs():
        nvidia_root = site_dir / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for pkg in _NVIDIA_DLL_PACKAGES:
            bin_dir = nvidia_root / pkg / "bin"
            if not bin_dir.is_dir():
                continue
            try:
                os.add_dll_directory(str(bin_dir))
                added.append(str(bin_dir))
            except OSError:
                log.debug(
                    "could not add CUDA DLL directory %s", bin_dir,
                    exc_info=True,
                )
    if added:
        log.info("registered %d NVIDIA CUDA DLL directory(ies)", len(added))
    return added


def _site_packages_dirs() -> list[Path]:
    """Candidate site-packages directories to scan for the nvidia wheels."""
    dirs: list[Path] = []
    try:
        import site

        for p in site.getsitepackages():
            dirs.append(Path(p))
        user_site = site.getusersitepackages()
        if isinstance(user_site, str):
            dirs.append(Path(user_site))
    except Exception:  # pragma: no cover - defensive
        log.debug("site.getsitepackages() failed", exc_info=True)
    # Fallback: the directory this package was installed into.
    dirs.append(Path(sys.prefix) / "Lib" / "site-packages")
    # De-dup while keeping order.
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique
