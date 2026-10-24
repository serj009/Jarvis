"""Install and bundle paths for dev runs vs PyInstaller builds."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """Directory containing Jarvis.exe (one-folder build) or the repo root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """PyInstaller _MEIPASS when frozen; else repo root."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", install_root()))
    return install_root()


def _asset_roots() -> tuple[Path, ...]:
    """Search order for bundled ML assets when frozen."""
    if is_frozen():
        return (install_root(), bundle_root())
    return (install_root(),)


def _first_existing_dir(*candidates: Path) -> Path | None:
    for path in candidates:
        if path.is_dir() and any(path.iterdir()):
            return path
    return None


def _first_existing_file(*candidates: Path) -> Path | None:
    for path in candidates:
        if path.is_file():
            return path
    return None


def default_voices_dir() -> Path:
    env = os.environ.get("JARVIS_PIPER_VOICES_DIR")
    if env:
        return Path(env)
    candidates = [root / "voices" for root in _asset_roots()]
    hit = _first_existing_dir(*candidates)
    if hit is not None:
        return hit
    return Path.home() / ".jarvis" / "voices"


def default_whisper_download_root() -> Path | None:
    candidates = [root / "models" / "whisper" for root in _asset_roots()]
    return _first_existing_dir(*candidates)


def default_silero_onnx_path() -> Path | None:
    candidates = [root / "models" / "silero_vad.onnx" for root in _asset_roots()]
    return _first_existing_file(*candidates)


def bundled_asset_report() -> dict[str, str]:
    """Human-readable paths for startup diagnostics."""
    return {
        "frozen": str(is_frozen()),
        "install_root": str(install_root()),
        "bundle_root": str(bundle_root()),
        "voices_dir": str(default_voices_dir()),
        "whisper_root": str(default_whisper_download_root() or "<missing>"),
        "silero_onnx": str(default_silero_onnx_path() or "<missing>"),
    }


# ---------------------------------------------------------------------------
# User data directory (config, memory, logs, lock, credentials, ...)
# ---------------------------------------------------------------------------
#
# Single source of truth for where Jarvis writes its own data. Every data
# module routes through user_data_dir() / data_path() — NOT os.environ nor
# Path.home()/".jarvis" directly (enforced by tests/test_architecture_rules).


def user_data_dir() -> Path:
    """Return the root directory for Jarvis's own persistent data.

    Resolution order:
      1. ``JARVIS_DATA_DIR`` env var, if set (treated as an absolute path).
      2. Windows: ``%APPDATA%/Jarvis``; when APPDATA is unset, fall back to
         ``~/AppData/Roaming/Jarvis``.
      3. Other platforms: ``~/.jarvis`` (unchanged historical location).

    Pure: does not create anything. Use data_path(..., create=True) to
    materialise directories.
    """
    env = os.environ.get("JARVIS_DATA_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis"
        return Path.home() / "AppData" / "Roaming" / "Jarvis"
    return Path.home() / ".jarvis"


def data_path(*parts: str, create: bool = False) -> Path:
    """Build a path under user_data_dir() from *parts*.

    With ``create=True`` the parent directory (for a file target with a
    suffix) or the path itself (a directory) is created with
    ``parents=True, exist_ok=True``. No parts → the data root itself.
    """
    root = user_data_dir()
    target = root.joinpath(*parts) if parts else root
    if create:
        to_make = target.parent if target.suffix else target
        try:
            to_make.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.debug("could not create data dir %s", to_make, exc_info=True)
    return target


def _data_dir_source() -> str:
    """Which resolution rule produced user_data_dir() — for the boot log."""
    if os.environ.get("JARVIS_DATA_DIR"):
        return "JARVIS_DATA_DIR"
    if sys.platform == "win32":
        return "APPDATA" if os.environ.get("APPDATA") else "home-fallback"
    return "home"


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def log_data_dir_choice() -> Path:
    """Log the chosen data dir once at startup and warn if it is unsafe.

    Emits a single INFO line naming the directory and which rule selected it.
    If the directory sits inside the system temp dir, JARVIS_DATA_DIR is not
    set, and we are not under pytest, emit a WARNING that memory will not
    persist — and, when a real ``~/AppData/Roaming/Jarvis/config.json``
    exists, point at it. Returns the resolved directory.
    """
    root = user_data_dir()
    source = _data_dir_source()
    log.info("data dir: %s (via %s)", root, source)

    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved = root.resolve()
        inside_temp = resolved == temp_root or temp_root in resolved.parents
    except OSError:
        inside_temp = False

    if (
        inside_temp
        and not os.environ.get("JARVIS_DATA_DIR")
        and not _running_under_pytest()
    ):
        msg = (
            "Папка данных во временной директории, память не сохранится: "
            f"{root}"
        )
        real = Path.home() / "AppData" / "Roaming" / "Jarvis" / "config.json"
        if sys.platform == "win32" and real.is_file():
            msg += f" (настоящая папка данных: {real.parent})"
        log.warning(msg)
    return root
