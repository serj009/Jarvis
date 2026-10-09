#!/usr/bin/env python3
"""J.A.R.V.I.S. first-run setup script.

Downloads all ML models, creates venv, installs dependencies, and runs
a smoke test. Idempotent — safe to run repeatedly. Skips already-
downloaded assets. Works offline when everything is cached.

Usage (from repo root):
    python setup_jarvis.py
    python setup_jarvis.py --whisper-model small
    python setup_jarvis.py --skip-smoke-test

Exit code: 0 when all critical steps passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VOICES_DIR = ROOT / "voices"
MODELS_DIR = ROOT / "models"
WHISPER_DIR = MODELS_DIR / "whisper"
OW_DIR = MODELS_DIR / "openwakeword"
SILERO_PATH = MODELS_DIR / "silero_vad.onnx"

OLLAMA_API = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"

MIN_PYTHON = (3, 10)

# All six Piper voices: 3 default (male) + 3 alternative.
# (voice_name, huggingface_base_url)
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
        # NOTE: Lada uses x_low quality (no medium model on HuggingFace).
        "uk_UA-lada-x_low",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "uk/uk_UA/lada/x_low",
    ),
]

# Custom trained voices — uploaded to your HuggingFace repo.
# After training a Piper voice, upload .onnx + .onnx.json to HuggingFace:
#   1. Create repo: huggingface.co/<your_username>/jarvis-custom-voices
#   2. Upload: ru_RU-baranov-custom.onnx, ru_RU-baranov-custom.onnx.json, etc.
#   3. Fill in the URLs below.
#   4. Users get custom voices automatically via: python setup_jarvis.py
#
# URL pattern for HuggingFace:
#   https://huggingface.co/<username>/jarvis-custom-voices/resolve/main/<filename>
#
# Uncomment and fill URLs when voices are ready:
CUSTOM_VOICES: list[tuple[str, str]] = [
    # (
    #     "ru_RU-baranov-custom",
    #     "https://huggingface.co/serhii/jarvis-custom-voices/resolve/main/"
    #     "ru_RU-baranov-custom",
    # ),
    # (
    #     "uk_UA-pecherytsya-custom",
    #     "https://huggingface.co/serhii/jarvis-custom-voices/resolve/main/"
    #     "uk_UA-pecherytsya-custom",
    # ),
    # (
    #     "en_US-baranov-cloned",
    #     "https://huggingface.co/serhii/jarvis-custom-voices/resolve/main/"
    #     "en_US-baranov-cloned",
    # ),
]

# ── Utilities ──────────────────────────────────────────────────

OK = "✅"
WARN = "⚠️"
FAIL = "❌"
SKIP = "⏭️"


def _header() -> None:
    print()
    print("═" * 55)
    print("  J.A.R.V.I.S. — Первичная настройка")
    print("═" * 55)
    print()


def _summary(results: list[tuple[str, str, str]]) -> None:
    """Print final summary table."""
    ok_count = sum(1 for _, s, _ in results if s == OK)
    warn_count = sum(1 for _, s, _ in results if s == WARN)
    fail_count = sum(1 for _, s, _ in results if s == FAIL)
    total = len(results)

    print()
    print("═" * 55)
    parts = []
    if ok_count:
        parts.append(f"{ok_count}/{total} {OK}")
    if warn_count:
        parts.append(f"{warn_count} {WARN}")
    if fail_count:
        parts.append(f"{fail_count} {FAIL}")
    print(f"  Результат: {'  |  '.join(parts)}")
    print("═" * 55)
    print()

    # Print actionable advice for warnings/failures
    for label, status, detail in results:
        if status in (WARN, FAIL) and detail:
            print(f"{status} {detail}")


def _python_exe() -> str:
    """Return the venv python path if venv exists, else current python."""
    if sys.platform == "win32":
        venv_py = VENV_DIR / "Scripts" / "python.exe"
    else:
        venv_py = VENV_DIR / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _pip_exe() -> str:
    """Return pip path inside venv."""
    if sys.platform == "win32":
        return str(VENV_DIR / "Scripts" / "pip.exe")
    return str(VENV_DIR / "bin" / "pip")


def _download_file(url: str, dest: Path, label: str = "") -> bool:
    """Download a file with progress indication. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Setup/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024
            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        print(
                            f"\r      Загрузка {label}: {mb:.1f}/{total_mb:.1f} MB ({pct}%)",
                            end="", flush=True,
                        )

            if total > 0:
                print()  # newline after progress
        return True
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"\n      Ошибка загрузки: {exc}")
        # Clean up partial download
        if dest.exists():
            dest.unlink()
        return False


def _get_json(url: str, timeout: float = 5) -> dict | None:
    """Fetch JSON from URL, return None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Setup/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


# ── Step functions ─────────────────────────────────────────────────────

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def step_python() -> tuple[str, str, str]:
    """[1/9] Check Python version."""
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"

    if ver < MIN_PYTHON:
        return (
            f"Python {ver_str}",
            FAIL,
            f"Требуется Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, "
            f"установлен {ver_str}",
        )
    return f"Python {ver_str}", OK, ""


def step_venv() -> tuple[str, str, str]:
    """[2/9] Create virtual environment if needed."""
    if VENV_DIR.is_dir() and Path(_python_exe()).is_file():
        return "Virtual environment", OK, f"(.venv/)"

    try:
        print(f"      Создание виртуального окружения в {VENV_DIR.name}/...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return "Virtual environment", OK, f"(.venv/) создано"
    except (subprocess.SubprocessError, OSError) as exc:
        return (
            "Virtual environment",
            FAIL,
            f"Не удалось создать venv: {exc}",
        )


def step_dependencies() -> tuple[str, str, str]:
    """[3/9] Install dependencies from pyproject.toml."""
    pip = _pip_exe()
    if not Path(pip).is_file():
        return "Зависимости (pip)", FAIL, "pip не найден — создайте venv сначала"

    try:
        # Check how many packages are already installed
        result = subprocess.run(
            [pip, "list", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        pre_count = len(json.loads(result.stdout)) if result.returncode == 0 else 0

        print("      Установка зависимостей (pip install -e .)...")
        proc = subprocess.run(
            [pip, "install", "-e", str(ROOT)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            # Show last 5 lines of stderr for diagnosis
            err_lines = proc.stderr.strip().splitlines()[-5:]
            err_msg = "\n      ".join(err_lines)
            return (
                "Зависимости (pip)",
                FAIL,
                f"pip install failed:\n      {err_msg}",
            )

        # Count installed packages
        result = subprocess.run(
            [pip, "list", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        post_count = len(json.loads(result.stdout)) if result.returncode == 0 else 0
        return "Зависимости (pip)", OK, f"({post_count} пакетов)"

    except subprocess.TimeoutExpired:
        return "Зависимости (pip)", FAIL, "Таймаут установки (>10 мин)"
    except (subprocess.SubprocessError, OSError) as exc:
        return "Зависимости (pip)", FAIL, f"Ошибка: {exc}"


def step_piper_voices() -> tuple[str, str, str]:
    """[4/9] Download all Piper TTS voice models."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    # Combine standard + custom voices into one download list.
    all_voices = list(PIPER_VOICES) + list(CUSTOM_VOICES)

    total_voices = len(all_voices)
    downloaded = 0
    cached = 0
    failed_voices: list[str] = []
    total_bytes = 0
    voice_results: list[tuple[str, str]] = []

    for voice_name, base_url in all_voices:
        voice_ok = True
        for suffix in (".onnx", ".onnx.json"):
            filename = f"{voice_name}{suffix}"
            dest = VOICES_DIR / filename
            url = f"{base_url}/{filename}"

            if dest.is_file() and dest.stat().st_size > 0:
                total_bytes += dest.stat().st_size
                continue

            if not _download_file(url, dest, filename):
                voice_ok = False
                break

            if dest.is_file():
                total_bytes += dest.stat().st_size

        if voice_ok:
            onnx = VOICES_DIR / f"{voice_name}.onnx"
            config = VOICES_DIR / f"{voice_name}.onnx.json"
            if onnx.is_file() and config.is_file():
                voice_results.append((voice_name, OK))
                if onnx.stat().st_size > 0:
                    downloaded += 1
            else:
                voice_results.append((voice_name, FAIL))
                failed_voices.append(voice_name)
        else:
            voice_results.append((voice_name, FAIL))
            failed_voices.append(voice_name)

    # Print voice tree
    total_mb = total_bytes / (1024 * 1024)
    for i, (vname, vstatus) in enumerate(voice_results):
        connector = "└──" if i == len(voice_results) - 1 else "├──"
        print(f"      {connector} {vname} {'.' * max(1, 22 - len(vname))} {vstatus}")

    ok_count = sum(1 for _, s in voice_results if s == OK)
    status_str = f"({ok_count} голосов, {total_mb:.0f} MB)"

    if failed_voices:
        return (
            "Голоса Piper",
            WARN if ok_count >= 3 else FAIL,
            f"Не удалось скачать: {', '.join(failed_voices)}",
        )
    return "Голоса Piper", OK, status_str


def step_whisper(model: str = "base") -> tuple[str, str, str]:
    """[5/9] Download faster-whisper model."""
    # Check if model is already cached
    # faster-whisper stores models in ~/.cache/huggingface/hub/ or download_root
    whisper_marker = WHISPER_DIR / "model.bin"
    # Also check the common HF cache location
    hf_cache = Path.home() / ".cache" / "huggingface"

    # Try to import and check if model is available
    try:
        python = _python_exe()
        check_code = f"""
import sys
try:
    from faster_whisper import WhisperModel
    import os
    download_root = r'{WHISPER_DIR}'
    os.makedirs(download_root, exist_ok=True)
    print("LOADING", flush=True)
    m = WhisperModel("{model}", device="cpu", compute_type="int8",
                     download_root=download_root)
    print("OK", flush=True)
except Exception as e:
    print(f"ERROR:{{e}}", flush=True)
    sys.exit(1)
"""
        print(f"      Загрузка модели faster-whisper {model}...")
        proc = subprocess.run(
            [python, "-c", check_code],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode == 0 and "OK" in proc.stdout:
            # Estimate size
            total_size = 0
            if WHISPER_DIR.is_dir():
                for f in WHISPER_DIR.rglob("*"):
                    if f.is_file():
                        total_size += f.stat().st_size
            size_mb = total_size / (1024 * 1024) if total_size > 0 else 150
            return (
                "Whisper (faster-whisper)",
                OK,
                f"({model}, {size_mb:.0f} MB)",
            )
        else:
            err = proc.stderr.strip().splitlines()[-3:] if proc.stderr else []
            err_msg = proc.stdout.strip() if not err else "\n      ".join(err)
            return (
                "Whisper (faster-whisper)",
                FAIL,
                f"Не удалось загрузить модель {model}: {err_msg}",
            )
    except subprocess.TimeoutExpired:
        return (
            "Whisper (faster-whisper)",
            FAIL,
            f"Таймаут загрузки модели (>10 мин)",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return "Whisper (faster-whisper)", FAIL, f"Ошибка: {exc}"


def step_silero_vad() -> tuple[str, str, str]:
    """[6/9] Ensure Silero VAD model is available."""
    try:
        python = _python_exe()
        check_code = """
import sys
try:
    import silero_vad
    import sysconfig
    from pathlib import Path
    purelib = Path(sysconfig.get_paths()["purelib"])
    onnx = purelib / "silero_vad" / "data" / "silero_vad.onnx"
    if onnx.is_file():
        print(f"OK:{onnx.stat().st_size}", flush=True)
    else:
        # Try to find it via the package
        pkg_dir = Path(silero_vad.__file__).resolve().parent
        onnx2 = pkg_dir / "data" / "silero_vad.onnx"
        if onnx2.is_file():
            print(f"OK:{onnx2.stat().st_size}", flush=True)
        else:
            print("MISSING", flush=True)
            sys.exit(1)
except ImportError:
    print("NOT_INSTALLED", flush=True)
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{e}", flush=True)
    sys.exit(1)
"""
        proc = subprocess.run(
            [python, "-c", check_code],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip().startswith("OK:"):
            return "Silero VAD", OK, ""
        elif "NOT_INSTALLED" in proc.stdout:
            return "Silero VAD", FAIL, "silero-vad не установлен (pip install -e .)"
        else:
            return "Silero VAD", FAIL, "silero_vad.onnx не найден"
    except (subprocess.SubprocessError, OSError) as exc:
        return "Silero VAD", FAIL, f"Ошибка: {exc}"


def step_openwakeword() -> tuple[str, str, str]:
    """[7/9] Ensure openWakeWord models are downloaded."""
    try:
        python = _python_exe()
        check_code = """
import sys
try:
    import openwakeword
    from pathlib import Path
    models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
    onnx_files = list(models_dir.glob("*.onnx")) if models_dir.is_dir() else []
    if onnx_files:
        print(f"OK:{len(onnx_files)}", flush=True)
    else:
        # Try downloading
        print("DOWNLOADING", flush=True)
        openwakeword.utils.download_models()
        onnx_files = list(models_dir.glob("*.onnx")) if models_dir.is_dir() else []
        if onnx_files:
            print(f"OK:{len(onnx_files)}", flush=True)
        else:
            print("EMPTY", flush=True)
            sys.exit(1)
except ImportError:
    print("NOT_INSTALLED", flush=True)
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{e}", flush=True)
    sys.exit(1)
"""
        proc = subprocess.run(
            [python, "-c", check_code],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                if line.startswith("OK:"):
                    count = line.split(":")[1]
                    return "OpenWakeWord", OK, f"({count} моделей)"
            return "OpenWakeWord", OK, ""
        elif "NOT_INSTALLED" in proc.stdout:
            return "OpenWakeWord", FAIL, "openwakeword не установлен"
        else:
            return "OpenWakeWord", FAIL, "Не удалось загрузить модели"
    except subprocess.TimeoutExpired:
        return "OpenWakeWord", FAIL, "Таймаут загрузки моделей (>5 мин)"
    except (subprocess.SubprocessError, OSError) as exc:
        return "OpenWakeWord", FAIL, f"Ошибка: {exc}"


def step_ollama() -> tuple[str, str, str]:
    """[8/9] Check Ollama availability and model status."""
    data = _get_json(f"{OLLAMA_API}/api/tags")
    if data is None:
        return (
            "Ollama",
            WARN,
            "Ollama не запущен. Установите: https://ollama.com и запустите: ollama serve",
        )

    models = [m.get("name", "") for m in data.get("models", [])]
    if DEFAULT_OLLAMA_MODEL in models or any(DEFAULT_OLLAMA_MODEL.split(":")[0] in m for m in models):
        return "Ollama", OK, f"({DEFAULT_OLLAMA_MODEL})"
    else:
        return (
            "Ollama",
            WARN,
            f"Модель {DEFAULT_OLLAMA_MODEL} не найдена. Запустите: ollama pull {DEFAULT_OLLAMA_MODEL}",
        )


def step_embeddings() -> tuple[str, str, str]:
    """[9/9] Download sentence-transformers embedding model for semantic memory search."""
    try:
        python = _python_exe()
        check_code = f"""
import sys
try:
    from sentence_transformers import SentenceTransformer
    import os
    # Try loading from cache first (offline).
    try:
        model = SentenceTransformer("{EMBEDDING_MODEL}", device="cpu",
                                     local_files_only=True)
        print("OK:cached", flush=True)
    except Exception:
        # Download the model.
        print("DOWNLOADING", flush=True)
        model = SentenceTransformer("{EMBEDDING_MODEL}", device="cpu")
        print("OK:downloaded", flush=True)
    # Quick sanity: encode a test sentence.
    vec = model.encode(["test"])
    print(f"DIM:{{vec.shape[1]}}", flush=True)
except ImportError:
    print("NOT_INSTALLED", flush=True)
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{{e}}", flush=True)
    sys.exit(1)
"""
        print(f"      Загрузка embedding модели ({EMBEDDING_MODEL.split('/')[-1]})...")
        proc = subprocess.run(
            [python, "-c", check_code],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode == 0 and "OK:" in proc.stdout:
            source = "кэш" if "cached" in proc.stdout else "скачано"
            return "Embeddings (FAISS)", OK, f"({EMBEDDING_MODEL.split('/')[-1]}, {source})"
        elif "NOT_INSTALLED" in proc.stdout:
            return (
                "Embeddings (FAISS)",
                WARN,
                "sentence-transformers не установлен. "
                "Установите: pip install -e \".[embeddings]\"",
            )
        else:
            err = proc.stderr.strip().splitlines()[-3:] if proc.stderr else []
            err_msg = proc.stdout.strip() if not err else "\n      ".join(err)
            return "Embeddings (FAISS)", WARN, f"Не удалось загрузить модель: {err_msg}"
    except subprocess.TimeoutExpired:
        return "Embeddings (FAISS)", WARN, "Таймаут загрузки модели (>10 мин)"
    except (subprocess.SubprocessError, OSError) as exc:
        return "Embeddings (FAISS)", WARN, f"Ошибка: {exc}"


# ── Main ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # Ensure console can print Unicode on Windows
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        # Also set console code page to UTF-8
        try:
            os.system("chcp 65001 >nul 2>&1")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="J.A.R.V.I.S. первичная настройка — скачивание моделей и зависимостей",
    )
    parser.add_argument(
        "--whisper-model",
        default="base",
        choices=["tiny", "tiny.en", "base", "base.en", "small", "small.en"],
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="Skip the final smoke test",
    )
    args = parser.parse_args(argv)

    _header()

    results: list[tuple[str, str, str]] = []
    steps = [
        ("1/9", "Python", step_python),
        ("2/9", "Virtual environment", step_venv),
        ("3/9", "Зависимости (pip)", step_dependencies),
        ("4/9", "Голоса Piper", step_piper_voices),
        ("5/9", "Whisper (faster-whisper)", lambda: step_whisper(args.whisper_model)),
        ("6/9", "Silero VAD", step_silero_vad),
        ("7/9", "OpenWakeWord", step_openwakeword),
        ("8/9", "Ollama + модель", step_ollama),
        ("9/9", "Embeddings (FAISS)", step_embeddings),
    ]

    for step_num, label, func in steps:
        # Print step header without newline — the function fills in the status
        sys.stdout.write(f"[{step_num}] {label} {'.' * max(1, 28 - len(label))} ")
        sys.stdout.flush()

        try:
            name, status, detail = func()
            # Print status on the same line
            status_text = status
            if detail and status == OK:
                status_text = f"{status} {detail}"
            print(status_text)
            results.append((name, status, detail))
        except Exception as exc:
            print(f"{FAIL}")
            print(f"      Неожиданная ошибка: {exc}")
            results.append((label, FAIL, f"Неожиданная ошибка: {exc}"))

        # If Python or venv failed, stop early — everything else depends on them
        if results[-1][1] == FAIL and step_num in ("1/9", "2/9"):
            print(f"\n{FAIL} Критическая ошибка — дальнейшая настройка невозможна.")
            _summary(results)
            return 1

    _summary(results)

    # Run smoke test if all critical steps passed
    if not args.skip_smoke_test:
        smoke_test = ROOT / "jarvis" / "dev" / "smoke_test.py"
        if smoke_test.is_file():
            print("─" * 55)
            print("  Smoke test (jarvis/dev/smoke_test.py)")
            print("─" * 55)
            try:
                subprocess.run(
                    [_python_exe(), "-m", "jarvis.dev.smoke_test"],
                    cwd=str(ROOT),
                    timeout=120,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                print(f"  {WARN} Smoke test не удался: {exc}")

    fail_count = sum(1 for _, s, _ in results if s == FAIL)
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
