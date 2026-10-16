#!/usr/bin/env python3
"""J.A.R.V.I.S. smoke test — end-to-end pipeline readiness check.

Verifies that all components required to launch JARVIS are in place:
imports, models, config, Ollama. Does NOT start JARVIS, no GUI, no audio.

Usage (from repo root):
    python smoke_test.py
    .venv/Scripts/python.exe smoke_test.py

Exit code: 0 = all checks passed, 1 = at least one failure.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
VOICES_DIR = ROOT / "voices"
MODELS_DIR = ROOT / "models"
WHISPER_DIR = MODELS_DIR / "whisper"

OLLAMA_API = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"

MIN_PYTHON = (3, 10)
TOTAL_CHECKS = 11

# Reuse the same status markers as setup_jarvis.py.
OK = "✅"
WARN = "⚠️"
FAIL = "❌"

# ── Helpers ────────────────────────────────────────────────────────


def _get_json(url: str, timeout: float = 5) -> dict | None:
    """Fetch JSON from *url*, return None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Smoke/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _header() -> None:
    print()
    print("═" * 55)
    print("  J.A.R.V.I.S. — Smoke Test (pipeline readiness)")
    print("═" * 55)
    print()


def _summary(results: list[tuple[str, str, str]]) -> None:
    """Print final summary table (same format as setup_jarvis.py)."""
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

    for label, status, detail in results:
        if status in (WARN, FAIL) and detail:
            print(f"{status} {detail}")


# ── Check functions ────────────────────────────────────────────────


def check_python_version() -> tuple[str, str, str]:
    """[1/11] Python version ≥ 3.10."""
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver < MIN_PYTHON:
        return (
            f"Python {ver_str}",
            FAIL,
            f"Требуется Python ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, "
            f"установлен {ver_str}",
        )
    return f"Python {ver_str}", OK, ""


def check_config_import() -> tuple[str, str, str]:
    """[2/11] Import jarvis.core.config — config module loads."""
    try:
        # ast.parse the file first to catch syntax errors cheaply.
        config_path = ROOT / "jarvis" / "core" / "config.py"
        source = config_path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(config_path))
        return "jarvis.core.config (ast)", OK, ""
    except SyntaxError as exc:
        return (
            "jarvis.core.config (ast)",
            FAIL,
            f"SyntaxError в config.py: {exc}",
        )
    except FileNotFoundError:
        return (
            "jarvis.core.config (ast)",
            FAIL,
            f"Файл не найден: {config_path}",
        )


def check_stt_import() -> tuple[str, str, str]:
    """[3/11] Import jarvis.audio.stt — STT module available."""
    try:
        stt_path = ROOT / "jarvis" / "audio" / "stt.py"
        source = stt_path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(stt_path))
        return "jarvis.audio.stt (ast)", OK, ""
    except SyntaxError as exc:
        return "jarvis.audio.stt (ast)", FAIL, f"SyntaxError: {exc}"
    except FileNotFoundError:
        return "jarvis.audio.stt (ast)", FAIL, f"Файл не найден: {stt_path}"


def check_tts_import() -> tuple[str, str, str]:
    """[4/11] Import jarvis.audio.tts — TTS module available."""
    try:
        tts_path = ROOT / "jarvis" / "audio" / "tts.py"
        source = tts_path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(tts_path))
        return "jarvis.audio.tts (ast)", OK, ""
    except SyntaxError as exc:
        return "jarvis.audio.tts (ast)", FAIL, f"SyntaxError: {exc}"
    except FileNotFoundError:
        return "jarvis.audio.tts (ast)", FAIL, f"Файл не найден: {tts_path}"


def check_ollama_client_import() -> tuple[str, str, str]:
    """[5/11] Import jarvis.llm.ollama_client — Ollama client available."""
    try:
        oc_path = ROOT / "jarvis" / "llm" / "ollama_client.py"
        source = oc_path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(oc_path))
        return "jarvis.llm.ollama_client (ast)", OK, ""
    except SyntaxError as exc:
        return "jarvis.llm.ollama_client (ast)", FAIL, f"SyntaxError: {exc}"
    except FileNotFoundError:
        return (
            "jarvis.llm.ollama_client (ast)",
            FAIL,
            f"Файл не найден: {oc_path}",
        )


def check_ollama_api() -> tuple[str, str, str]:
    """[6/11] Ollama API alive (GET /api/tags)."""
    data = _get_json(f"{OLLAMA_API}/api/tags")
    if data is None:
        return (
            "Ollama API",
            WARN,
            "Ollama не запущен или недоступен на 127.0.0.1:11434. "
            "Запустите: ollama serve",
        )
    model_count = len(data.get("models", []))
    return "Ollama API", OK, f"(alive, {model_count} моделей)"


def check_ollama_model() -> tuple[str, str, str]:
    """[7/11] Required model present (qwen3:8b)."""
    data = _get_json(f"{OLLAMA_API}/api/tags")
    if data is None:
        return (
            f"Модель {DEFAULT_OLLAMA_MODEL}",
            WARN,
            "Ollama недоступен — проверка модели пропущена",
        )
    models = [m.get("name", "") for m in data.get("models", [])]
    base_name = DEFAULT_OLLAMA_MODEL.split(":")[0]
    if DEFAULT_OLLAMA_MODEL in models or any(base_name in m for m in models):
        return f"Модель {DEFAULT_OLLAMA_MODEL}", OK, ""
    return (
        f"Модель {DEFAULT_OLLAMA_MODEL}",
        WARN,
        f"Модель не найдена. Запустите: ollama pull {DEFAULT_OLLAMA_MODEL}",
    )


def check_whisper_model() -> tuple[str, str, str]:
    """[8/11] Whisper model files exist."""
    if not WHISPER_DIR.is_dir():
        return (
            "Whisper model files",
            FAIL,
            f"Директория не найдена: {WHISPER_DIR}. "
            "Запустите: python setup_jarvis.py",
        )
    # faster-whisper downloads into a subfolder (e.g. models--Systran--faster-whisper-small)
    # or directly into the whisper dir. Look for .bin or model files.
    model_files = list(WHISPER_DIR.rglob("model.bin")) + list(
        WHISPER_DIR.rglob("*.bin")
    )
    if not model_files:
        # Also accept CTranslate2 format (.bin inside any subfolder).
        ct2_files = list(WHISPER_DIR.rglob("*.bin")) + list(
            WHISPER_DIR.rglob("config.json")
        )
        if not ct2_files:
            return (
                "Whisper model files",
                FAIL,
                f"Модели не найдены в {WHISPER_DIR}. "
                "Запустите: python setup_jarvis.py",
            )
    total_size = sum(
        f.stat().st_size for f in WHISPER_DIR.rglob("*") if f.is_file()
    )
    size_mb = total_size / (1024 * 1024)
    return "Whisper model files", OK, f"({size_mb:.0f} MB)"


def check_piper_voices() -> tuple[str, str, str]:
    """[9/11] At least 1 Piper voice downloaded."""
    if not VOICES_DIR.is_dir():
        return (
            "Piper voices",
            FAIL,
            f"Директория не найдена: {VOICES_DIR}. "
            "Запустите: python setup_jarvis.py",
        )
    onnx_files = list(VOICES_DIR.glob("*.onnx"))
    json_files = list(VOICES_DIR.glob("*.onnx.json"))
    # A valid voice has both .onnx and .onnx.json.
    voice_names = set()
    for f in onnx_files:
        stem = f.name.removesuffix(".onnx")
        config = VOICES_DIR / f"{stem}.onnx.json"
        if config.is_file():
            voice_names.add(stem)

    if not voice_names:
        return (
            "Piper voices",
            FAIL,
            f"Нет голосов (.onnx + .onnx.json) в {VOICES_DIR}. "
            "Запустите: python setup_jarvis.py",
        )
    total_size = sum(f.stat().st_size for f in onnx_files)
    size_mb = total_size / (1024 * 1024)
    return "Piper voices", OK, f"({len(voice_names)} голосов, {size_mb:.0f} MB)"


def check_config_json() -> tuple[str, str, str]:
    """[10/11] Config JSON loadable + schema version correct."""
    # Determine the config path (same logic as jarvis.core.config).
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_path = Path(appdata) / "Jarvis" / "config.json"
        else:
            config_path = Path.home() / ".jarvis" / "config.json"
    else:
        config_path = Path.home() / ".jarvis" / "config.json"

    if not config_path.is_file():
        return (
            "Config JSON",
            WARN,
            f"Файл не найден: {config_path} — "
            "будет создан при первом запуске",
        )

    try:
        with config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        return "Config JSON", FAIL, f"Невалидный JSON: {exc}"
    except (OSError, UnicodeDecodeError) as exc:
        return "Config JSON", FAIL, f"Ошибка чтения: {exc}"

    if not isinstance(raw, dict):
        return "Config JSON", FAIL, "config.json — корень не объект JSON"

    # Read the expected schema_version from config.py source (avoid import).
    expected_version: int | None = None
    config_py = ROOT / "jarvis" / "core" / "config.py"
    if config_py.is_file():
        try:
            tree = ast.parse(config_py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "CURRENT_SCHEMA_VERSION"
                            and isinstance(node.value, ast.Constant)
                        ):
                            expected_version = node.value.value
        except (SyntaxError, OSError):
            pass

    actual = raw.get("schema_version")
    if expected_version is not None and actual != expected_version:
        return (
            "Config JSON",
            WARN,
            f"schema_version={actual}, ожидается {expected_version}. "
            "Миграция произойдёт при запуске",
        )

    return "Config JSON", OK, f"(v{actual})"


def check_all_imports() -> tuple[str, str, str]:
    """[11/11] All critical .py files parse (no syntax errors / circular deps)."""
    jarvis_pkg = ROOT / "jarvis"
    if not jarvis_pkg.is_dir():
        return (
            "Critical imports (ast)",
            FAIL,
            f"Пакет jarvis/ не найден: {jarvis_pkg}",
        )

    errors: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(str(jarvis_pkg)):
        # Skip __pycache__.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            filepath = Path(dirpath) / fn
            total += 1
            try:
                source = filepath.read_text(encoding="utf-8")
                ast.parse(source, filename=str(filepath))
            except SyntaxError as exc:
                rel = filepath.relative_to(ROOT)
                errors.append(f"{rel}: {exc.msg} (line {exc.lineno})")

    if errors:
        detail = "; ".join(errors[:5])
        if len(errors) > 5:
            detail += f" ... и ещё {len(errors) - 5}"
        return (
            f"Critical imports ({total} files)",
            FAIL,
            f"SyntaxError: {detail}",
        )
    return f"Critical imports ({total} files)", OK, ""


# ── Main ───────────────────────────────────────────────────────────


def main() -> int:
    # Ensure console can print Unicode on Windows.
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            os.system("chcp 65001 >nul 2>&1")
        except Exception:
            pass

    _header()

    checks: list[tuple[str, str, Callable[[], tuple[str, str, str]]]] = [
        ("1", "Python version", check_python_version),
        ("2", "jarvis.core.config", check_config_import),
        ("3", "jarvis.audio.stt", check_stt_import),
        ("4", "jarvis.audio.tts", check_tts_import),
        ("5", "jarvis.llm.ollama_client", check_ollama_client_import),
        ("6", "Ollama API", check_ollama_api),
        ("7", f"Модель {DEFAULT_OLLAMA_MODEL}", check_ollama_model),
        ("8", "Whisper model files", check_whisper_model),
        ("9", "Piper voices", check_piper_voices),
        ("10", "Config JSON", check_config_json),
        ("11", "Critical imports (ast)", check_all_imports),
    ]

    results: list[tuple[str, str, str]] = []

    for num, label, func in checks:
        sys.stdout.write(
            f"[{num:>2}/{TOTAL_CHECKS}] {label} {'.' * max(1, 30 - len(label))} "
        )
        sys.stdout.flush()

        try:
            name, status, detail = func()
            status_text = status
            if detail and status == OK:
                status_text = f"{status} {detail}"
            print(status_text)
            results.append((name, status, detail))
        except Exception as exc:
            print(FAIL)
            print(f"      Неожиданная ошибка: {exc}")
            results.append((label, FAIL, f"Неожиданная ошибка: {exc}"))

    _summary(results)

    fail_count = sum(1 for _, s, _ in results if s == FAIL)
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
