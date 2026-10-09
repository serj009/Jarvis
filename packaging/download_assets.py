"""Download/copy ML assets into packaging/bundle/ for PyInstaller.

Run from the repo root before building:
    python packaging/download_assets.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "packaging" / "bundle"
VOICES = BUNDLE / "voices"
WHISPER = BUNDLE / "models" / "whisper"
OW_DIR = BUNDLE / "models" / "openwakeword"

# All six Piper voices: 3 default (male) + 3 alternative (female/alt).
# Default voices are used out of the box; alternatives are available
# for user selection via config or Settings UI.
# Each entry: (voice_name, huggingface_base_url)
PIPER_VOICES: list[tuple[str, str]] = [
    (
        "en_GB-alan-medium",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "en/en_GB/alan/medium",
    ),
    (
        "en_US-lessac-medium",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "en/en_US/lessac/medium",
    ),
    (
        "ru_RU-dmitri-medium",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "ru/ru_RU/dmitri/medium",
    ),
    (
        "ru_RU-irina-medium",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "ru/ru_RU/irina/medium",
    ),
    (
        "uk_UA-mykyta-medium",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "uk/uk_UA/mykyta/medium",
    ),
    (
        # NOTE: Lada uses x_low quality (no medium model available on HF).
        "uk_UA-lada-x_low",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "uk/uk_UA/lada/x_low",
    ),
]


def _ensure_openwakeword() -> None:
    import openwakeword

    openwakeword.utils.download_models()
    src = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
    OW_DIR.mkdir(parents=True, exist_ok=True)
    for onnx in src.glob("*.onnx"):
        dest = OW_DIR / onnx.name
        if not dest.exists():
            shutil.copy2(onnx, dest)
    print(f"openWakeWord models -> {OW_DIR}")


def _ensure_silero() -> None:
    import sysconfig

    purelib = Path(sysconfig.get_paths()["purelib"])
    src = purelib / "silero_vad" / "data" / "silero_vad.onnx"
    if not src.is_file():
        raise FileNotFoundError(f"silero_vad.onnx not found at {src}")
    dest = BUNDLE / "models" / "silero_vad.onnx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"silero VAD -> {dest}")


def _ensure_whisper() -> None:
    from faster_whisper import WhisperModel

    WHISPER.mkdir(parents=True, exist_ok=True)
    print("Downloading faster-whisper base.en (may take a few minutes)...")
    WhisperModel("base.en", device="cpu", compute_type="int8", download_root=str(WHISPER))
    print(f"whisper base.en -> {WHISPER}")


def _ensure_piper_voice() -> None:
    import httpx

    VOICES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for voice_name, base_url in PIPER_VOICES:
            for suffix in (".onnx", ".onnx.json"):
                name = f"{voice_name}{suffix}"
                url = f"{base_url}/{name}"
                dest = VOICES / name
                if dest.is_file() and dest.stat().st_size > 0:
                    print(f"piper voice cached: {dest.name}")
                    continue
                print(f"Downloading {url} ...")
                resp = client.get(url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                print(f"  -> {dest}")


def main() -> int:
    BUNDLE.mkdir(parents=True, exist_ok=True)
    _ensure_openwakeword()
    _ensure_silero()
    _ensure_whisper()
    _ensure_piper_voice()
    print("Asset bundle ready:", BUNDLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
