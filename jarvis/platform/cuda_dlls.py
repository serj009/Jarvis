r"""Make pip-installed NVIDIA CUDA DLLs importable on Windows.

The ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` wheels drop their DLLs
under ``site-packages/nvidia/<pkg>/bin`` rather than on the system PATH.
ctranslate2 (and therefore faster-whisper) dlopen these at import time, so
on Windows they must be discoverable BEFORE ctranslate2 is imported, or CUDA
silently falls back to CPU.

Two registrations are needed (confirmed on the user's machine):
``os.add_dll_directory`` alone is NOT enough — ctranslate2 loads the CUDA
libraries via a plain ``LoadLibrary`` that honours the process ``PATH``, so
the directories must ALSO be prepended to ``os.environ["PATH"]``. With the
nvidia bin dirs at the front of PATH the model loads on CUDA ("CUDA OK");
without them it fails with ``Library cublas64_12.dll is not found``.

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

# Key DLLs we expect in the nvidia wheel bin dirs — logged on success so the
# live log shows what was actually found for the CUDA backend.
_KEY_DLLS = ("cublas64_12.dll", "cudnn64_9.dll", "nvrtc64_120_0.dll")

# Cache of the directories added during this process. ``None`` means the
# scan has not run yet; a list (possibly empty) means it has — so a second
# call is a cheap no-op and never logs or mutates PATH twice.
_added_dirs: list[str] | None = None


def add_cuda_dll_directories() -> list[str]:
    """Register NVIDIA wheel DLL directories for the current process.

    Scans every ``site-packages/nvidia/*/bin`` directory and, on Windows:
      1. registers it via ``os.add_dll_directory``, and
      2. prepends it to ``os.environ["PATH"]`` (ctranslate2 loads the CUDA
         libs with a PATH-honouring ``LoadLibrary``, so step 1 alone is not
         sufficient).

    Idempotent: the result is cached, so repeated calls (the STT engine calls
    this from two places) do nothing after the first and log only once.
    Returns the list of directories added (empty on non-Windows or when no
    NVIDIA wheels are installed). Never raises — a failure to register a
    directory is logged at debug and skipped, leaving the CPU path intact.
    """
    global _added_dirs
    if _added_dirs is not None:
        return _added_dirs
    if sys.platform != "win32":
        _added_dirs = []
        return _added_dirs
    if not hasattr(os, "add_dll_directory"):  # pragma: no cover - win-only API
        _added_dirs = []
        return _added_dirs

    looked_in: list[str] = []
    bin_dirs: list[Path] = []
    for site_dir in _site_packages_dirs():
        nvidia_root = site_dir / "nvidia"
        looked_in.append(str(nvidia_root))
        if not nvidia_root.is_dir():
            continue
        # Scan ALL sub-packages' bin dirs rather than a hard-coded list, so a
        # renamed/added wheel (cuda_nvrtc, cuda_runtime, ...) is picked up
        # automatically.
        for child in sorted(nvidia_root.iterdir()):
            bin_dir = child / "bin"
            if bin_dir.is_dir():
                bin_dirs.append(bin_dir)

    added: list[str] = []
    for bin_dir in bin_dirs:
        resolved = str(bin_dir)
        try:
            os.add_dll_directory(resolved)
        except OSError:
            log.debug("could not add CUDA DLL directory %s", bin_dir, exc_info=True)
            continue
        _prepend_to_path(resolved)
        added.append(resolved)

    if added:
        found = _key_dlls_found(bin_dirs)
        log.info(
            "registered %d NVIDIA CUDA DLL directory(ies); key DLLs found: %s",
            len(added),
            ", ".join(found) if found else "none",
        )
    else:
        log.info(
            "NVIDIA DLL dirs: none found; looked in: %s",
            ", ".join(looked_in) or "<no site-packages>",
        )

    _added_dirs = added
    return added


def _prepend_to_path(directory: str) -> None:
    """Prepend *directory* to ``os.environ['PATH']`` if not already leading.

    Case-insensitive de-dup (Windows paths) so repeated runs do not grow PATH.
    """
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if parts and parts[0].lower() == directory.lower():
        return  # already at the front
    # Drop any existing occurrence (anywhere) before prepending.
    parts = [p for p in parts if p.lower() != directory.lower()]
    os.environ["PATH"] = os.pathsep.join([directory, *parts])


def _key_dlls_found(bin_dirs: list[Path]) -> list[str]:
    found: list[str] = []
    for name in _KEY_DLLS:
        for bin_dir in bin_dirs:
            if (bin_dir / name).is_file():
                found.append(name)
                break
    return found


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


def _reset_cache_for_tests() -> None:
    """Test helper: clear the process cache so a fresh scan can be exercised."""
    global _added_dirs
    _added_dirs = None


# The CUDA runtime DLL the faster-whisper CUDA backend needs first; its
# presence+loadability is the single best signal that the GPU path will work.
_PROBE_DLL = "cublas64_12.dll"


def probe_cuda_libraries() -> tuple[bool, str]:
    """Try to actually LOAD the key CUDA DLL on Windows (read-only probe).

    Registers the NVIDIA wheel dirs first (so the loader can find the DLL),
    then attempts ``ctypes.WinDLL(cublas64_12.dll)``. Returns ``(ok, detail)``:

      * ``(True, "<dll> loaded")`` when the library loads — the real signal
        that ctranslate2's CUDA backend will work, catching the live
        ``cublas64_12.dll is not found`` failure that a mere "device=cuda"
        resolution misses;
      * ``(False, "<reason>")`` when it cannot load (not Windows, no wheels,
        or the DLL is missing/unloadable).

    Never raises. ``ctypes.WinDLL`` (NOT ``ctypes.windll``) is used, so this
    stays clear of the win32 architecture gate while still living under
    ``jarvis/platform/`` where Windows-specific code belongs.
    """
    if sys.platform != "win32":
        return False, "not Windows"

    add_cuda_dll_directories()  # ensure PATH / dll dirs are registered

    import ctypes

    try:
        ctypes.WinDLL(_PROBE_DLL)  # type: ignore[attr-defined]
    except OSError as exc:
        return False, (
            f"{_PROBE_DLL} failed to load ({exc}); install the gpu extra "
            "(CUDA 12 / cuDNN 9 runtime wheels)"
        )
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"{_PROBE_DLL} load error: {exc}"
    return True, f"{_PROBE_DLL} loaded"
