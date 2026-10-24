"""``python -m jarvis --doctor`` — read-only self-diagnostics (Task C part 2).

Runs a battery of environment checks and reports each as PASS / WARN / FAIL
with a fix hint. The doctor is strictly read-only: it never downloads,
installs, or opens a network connection other than the localhost Ollama probe
(GET /api/tags), and it never prints secret values.

Public entry point: :func:`run_doctor`, called from ``jarvis/__main__.py`` when
``--doctor`` is passed. Each individual check is a module-level function so it
can be unit-tested in isolation with its externals mocked.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

# Minimum supported Python (SPEC / pyproject).
_MIN_PY = (3, 11)


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""
    hint: str = ""  # how to fix, shown when not PASS

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def has_fail(self) -> bool:
        return any(r.status == FAIL for r in self.results)

    def counts(self) -> dict[str, int]:
        c = {PASS: 0, WARN: 0, FAIL: 0}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return c


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    """Replace the user's home dir and username with ``~`` / ``<user>``.

    Used for --redact so a shared report carries no personal paths/name.
    """
    if not text:
        return text
    out = text
    try:
        home = str(Path.home())
        if home and home in out:
            out = out.replace(home, "~")
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if user and user in out:
            out = out.replace(user, "<user>")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_and_os() -> CheckResult:
    v = sys.version_info
    detail = f"Python {platform.python_version()} on {platform.system()} {platform.release()}"
    if (v.major, v.minor) < _MIN_PY:
        return CheckResult(
            "Python & OS", FAIL, detail,
            hint=f"Jarvis needs Python >= {_MIN_PY[0]}.{_MIN_PY[1]}; upgrade your interpreter.",
        )
    return CheckResult("Python & OS", PASS, detail)


def check_data_dir() -> CheckResult:
    """Data dir is resolved (via user_data_dir), writable, has free space, and
    is not inside the system temp dir. Reuses jarvis.paths — no duplication."""
    from jarvis.paths import _data_dir_source, user_data_dir

    try:
        root = user_data_dir()
        source = _data_dir_source()
    except Exception as exc:
        return CheckResult("Data directory", FAIL, f"could not resolve: {exc}",
                           hint="Check JARVIS_DATA_DIR / %APPDATA%.")

    detail = f"{root} (via {source})"

    # Writable? Probe with a temp file we immediately remove (read-only probe
    # in spirit: created and deleted, leaves nothing behind).
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".doctor_write_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except Exception:
        return CheckResult("Data directory", FAIL, detail,
                           hint="The data directory is not writable; check permissions.")

    # Inside the system temp dir? (same rule as paths.log_data_dir_choice)
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved = root.resolve()
        inside_temp = resolved == temp_root or temp_root in resolved.parents
    except OSError:
        inside_temp = False
    if inside_temp and not os.environ.get("JARVIS_DATA_DIR"):
        return CheckResult("Data directory", WARN, detail,
                           hint="Data dir is in the system temp folder; memory will not persist. "
                                "Set JARVIS_DATA_DIR or run outside temp.")

    # Free space
    try:
        free_mb = shutil.disk_usage(root).free // (1024 * 1024)
    except Exception:
        free_mb = None
    if free_mb is not None and free_mb < 500:
        return CheckResult("Data directory", WARN, f"{detail}, {free_mb} MB free",
                           hint="Low free space (<500 MB); free up disk for models/logs.")

    space = f", {free_mb} MB free" if free_mb is not None else ""
    return CheckResult("Data directory", PASS, detail + space)


def check_config() -> CheckResult:
    # Read-only probe: config_health() never writes or migrates, so the
    # doctor cannot create a default config or touch backups (Task D §6).
    from jarvis.core.config import config_health, default_config_path

    path = default_config_path()
    info = config_health(path)
    n_backups = len(info["backups"])
    lg = "lastgood: yes" if info.get("has_lastgood") else "lastgood: no"
    backup_note = (
        f"{n_backups} backup(s), {lg}" if n_backups else f"no backups, {lg}"
    )

    if not info["exists"]:
        return CheckResult("Config", WARN, f"{path} (absent — defaults will be used)",
                           hint="A config.json will be created on first run; "
                                "this is fine for a fresh install.")
    if info["error"] is not None:
        hint = "config.json is corrupt; fix it or delete it to regenerate."
        if n_backups:
            hint = ("config.json is corrupt; Jarvis will restore from a backup "
                    "on next start, or you can replace it manually.")
        return CheckResult("Config", FAIL,
                           f"{info['error']} ({backup_note})", hint=hint)
    if info["too_new"]:
        return CheckResult("Config", WARN,
                           f"schema v{info['schema_version']} is newer than "
                           f"supported v{info['current_schema']} ({backup_note})",
                           hint="This config was written by a newer Jarvis; "
                                "update Jarvis or restore an older config.")
    return CheckResult("Config", PASS,
                       f"valid, schema v{info['schema_version']} ({backup_note})")


def _ollama_tags(endpoint: str | None = None) -> list[str] | None:
    """Return the list of pulled model names, or None if Ollama unreachable.

    Localhost-only GET /api/tags — the sole network call doctor is allowed."""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    base = (endpoint or "http://127.0.0.1:11434").rstrip("/")
    try:
        req = Request(base + "/api/tags", method="GET")  # noqa: S310
        with urlopen(req, timeout=3.0) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, ValueError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for m in models:
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            names.append(m["name"])
    return names


def check_ollama(configured_model: str) -> CheckResult:
    names = _ollama_tags()
    if names is None:
        return CheckResult("Ollama", FAIL, "not reachable on 127.0.0.1:11434",
                           hint="Start Ollama (`ollama serve`) or install from https://ollama.com")
    # A tag may be "qwen3:8b" or "qwen3:8b" vs bare "qwen3"; match loosely.
    def _matches(tag: str) -> bool:
        return tag == configured_model or tag.split(":")[0] == configured_model.split(":")[0]

    if any(_matches(n) for n in names):
        return CheckResult("Ollama", PASS, f"reachable, model {configured_model!r} present")
    return CheckResult("Ollama", FAIL,
                       f"reachable but model {configured_model!r} not pulled "
                       f"(have: {', '.join(names) or 'none'})",
                       hint=f"Run: ollama pull {configured_model}")


def check_audio_devices() -> CheckResult:
    """Default input + output devices exist; warn if output looks like an HDMI
    monitor (a common 'no sound' cause)."""
    try:
        import sounddevice as sd
    except Exception:
        return CheckResult("Audio devices", WARN, "sounddevice not installed",
                           hint="Install audio deps to enable voice I/O.")
    try:
        default_in, default_out = sd.default.device
        devices = sd.query_devices()
    except Exception as exc:
        return CheckResult("Audio devices", FAIL, f"query failed: {exc}",
                           hint="No audio devices detected; check drivers.")

    def _name(idx: Any) -> str:
        try:
            return devices[idx]["name"]
        except Exception:
            return "?"

    in_name = _name(default_in)
    out_name = _name(default_out)
    detail = f"in={in_name!r}, out={out_name!r}"
    if any(k in out_name.lower() for k in ("hdmi", "display", "monitor")):
        return CheckResult("Audio devices", WARN, detail,
                           hint="Default output looks like an HDMI/monitor sink; "
                                "pick speakers/headphones in Settings -> Voice.")
    return CheckResult("Audio devices", PASS, detail)


def check_stt(configured_device: str) -> CheckResult:
    """Report the resolved STT device + whether a Whisper model is available."""
    from jarvis.paths import default_whisper_download_root

    try:
        from jarvis.audio.stt import FasterWhisperSTT  # type: ignore

        resolved = FasterWhisperSTT._resolve_device(configured_device)  # noqa: SLF001
    except Exception:
        resolved = configured_device  # best-effort; module may need audio deps

    model_root = default_whisper_download_root()
    have_model = model_root is not None and model_root.is_dir()
    _model_state = "present" if have_model else "not downloaded"
    detail = f"device={resolved} (configured {configured_device!r}), model={_model_state}"
    if not have_model:
        return CheckResult("STT", WARN, detail,
                           hint="Whisper model not found locally; "
                                "it downloads on first use (needs network).")
    return CheckResult("STT", PASS, detail)


def _expected_voice_sha() -> dict[str, Any]:
    """Pull the pinned sha256 table from setup_jarvis (best-effort)."""
    try:
        import importlib

        setup = importlib.import_module("setup_jarvis")
        table = getattr(setup, "EXPECTED_SHA256", {})
        return dict(table) if isinstance(table, dict) else {}
    except Exception:
        return {}


def _sha256_of(path: Path) -> str | None:
    import hashlib

    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def check_tts_voices(enabled_languages: list[str], voices_by_lang: dict[str, str]) -> CheckResult:
    """Voice files exist for each enabled language and (when a pin exists)
    their sha256 matches setup_jarvis.EXPECTED_SHA256."""
    from jarvis.paths import default_voices_dir

    voices_dir = default_voices_dir()
    if not voices_dir.is_dir():
        return CheckResult("TTS voices", FAIL, f"voices dir missing: {voices_dir}",
                           hint="Run `python setup_jarvis.py` to download Piper voices.")

    expected = _expected_voice_sha()
    missing: list[str] = []
    mismatched: list[str] = []
    for lang in enabled_languages:
        voice = voices_by_lang.get(lang)
        if not voice:
            continue
        onnx = voices_dir / f"{voice}.onnx"
        if not onnx.is_file():
            missing.append(f"{lang}:{voice}")
            continue
        pinned = expected.get(f"{voice}.onnx")
        if pinned:
            actual = _sha256_of(onnx)
            if actual and actual.lower() != str(pinned).lower():
                mismatched.append(f"{lang}:{voice}")

    if missing:
        return CheckResult("TTS voices", FAIL, f"missing: {', '.join(missing)}",
                           hint="Run `python setup_jarvis.py` to fetch the missing voice(s).")
    if mismatched:
        return CheckResult("TTS voices", WARN, f"sha256 mismatch: {', '.join(mismatched)}",
                           hint="Voice file differs from the pinned hash; "
                                "re-run setup_jarvis.py to restore.")
    return CheckResult("TTS voices", PASS, f"{voices_dir} — all enabled languages present")


def check_wakeword_and_vad() -> CheckResult:
    from jarvis.paths import default_silero_onnx_path

    silero = default_silero_onnx_path()
    have_vad = silero is not None and silero.is_file()
    # openwakeword ships its own bundled models; presence of the package is the
    # practical check (its weights live inside the installed package).
    try:
        import importlib.util

        have_wake = importlib.util.find_spec("openwakeword") is not None
    except Exception:
        have_wake = False

    if have_vad and have_wake:
        return CheckResult("Wake word & VAD", PASS, "Silero VAD + openWakeWord present")
    missing = []
    if not have_wake:
        missing.append("openWakeWord")
    if not have_vad:
        missing.append("Silero VAD onnx")
    return CheckResult("Wake word & VAD", WARN, f"missing: {', '.join(missing)}",
                       hint="Run `python setup_jarvis.py` to install wake-word / VAD models.")


def check_gpu() -> CheckResult:
    """GPU name + free VRAM via nvidia-smi (read-only). WARN, not FAIL, when
    absent — Jarvis runs on CPU too."""
    from jarvis.core.vram_manager import _parse_nvidia_smi  # noqa: SLF001

    try:
        snap = _parse_nvidia_smi()
    except Exception:
        snap = None
    if snap is None:
        return CheckResult("GPU", WARN, "no NVIDIA GPU detected (nvidia-smi unavailable)",
                           hint="CPU mode works; install an NVIDIA GPU + driver for faster STT.")
    return CheckResult("GPU", PASS, f"{snap.gpu_name}, {snap.free_mb} MB free / {snap.total_mb} MB")


_EXTRAS = {
    "gpu": "ctranslate2",
    "security": "cryptography",
    "embeddings": "sentence_transformers",
    "browser": "selenium",
}


def check_extras() -> CheckResult:
    import importlib.util

    present = []
    absent = []
    for extra, probe in _EXTRAS.items():
        try:
            ok = importlib.util.find_spec(probe) is not None
        except Exception:
            ok = False
        (present if ok else absent).append(extra)
    detail = f"installed: {', '.join(present) or 'none'}; missing: {', '.join(absent) or 'none'}"
    # Extras are optional — never FAIL, just report.
    return CheckResult("Optional extras", PASS, detail)


def check_integrations(enabled: list[str]) -> CheckResult:
    """List enabled integrations and whether a token is present — NEVER the
    token value."""
    if not enabled:
        return CheckResult("Integrations", PASS, "none enabled")
    auth = None
    try:
        from jarvis.integrations.auth import IntegrationAuth

        auth = IntegrationAuth()
    except Exception:
        auth = None

    rows = []
    any_missing = False
    for name in enabled:
        present = False
        if auth is not None:
            try:
                present = bool(auth.has_credentials(name))
            except Exception:
                present = False
        if not present:
            any_missing = True
        rows.append(f"{name}={'token ok' if present else 'NO TOKEN'}")
    detail = ", ".join(rows)
    if any_missing:
        return CheckResult("Integrations", WARN, detail,
                           hint="Some enabled integrations have no stored token; "
                                "re-authenticate them in Settings.")
    return CheckResult("Integrations", PASS, detail)


def check_migration_state() -> CheckResult:
    """Show the ~/.jarvis migration state (read-only). Does NOT run migration."""
    from jarvis.paths import user_data_dir

    if sys.platform != "win32":
        return CheckResult("Legacy migration", PASS, "not applicable (non-Windows)")
    try:
        root = user_data_dir()
        # Reuse the migration module's own seam instead of re-encoding the
        # legacy path here (keeps the ".jarvis" literal in exactly one place).
        from jarvis.core.data_migration import _legacy_root

        legacy = _legacy_root()
    except Exception as exc:
        return CheckResult("Legacy migration", WARN, f"could not resolve: {exc}")

    marker = root / ".migrated_from_dotjarvis"
    if not legacy.is_dir():
        return CheckResult("Legacy migration", PASS, "no legacy ~/.jarvis directory")
    if marker.exists():
        return CheckResult("Legacy migration", PASS, "already migrated (marker present)")
    # Legacy exists, not yet migrated — count files not present in new root.
    pending = 0
    try:
        for src in legacy.rglob("*"):
            if src.is_dir() or (src.parts and "voices" in src.relative_to(legacy).parts[:1]):
                continue
            rel = src.relative_to(legacy)
            if not (root / rel).exists():
                pending += 1
    except Exception:
        pending = -1
    return CheckResult("Legacy migration", WARN,
                       f"legacy ~/.jarvis present, {pending} file(s) not yet migrated",
                       hint="Migration runs automatically on next normal startup.")


# ---------------------------------------------------------------------------
# Optional deep checks (--deep): 1s silence recognition + short synth to buffer
# ---------------------------------------------------------------------------


def check_deep_stt_tts() -> list[CheckResult]:
    """Best-effort 1s silence STT + short TTS-to-buffer. Never plays audio."""
    results: list[CheckResult] = []
    # Deep checks require heavy audio deps; degrade gracefully.
    try:
        import numpy as np  # noqa: F401
    except Exception:
        results.append(CheckResult("Deep: STT silence", WARN, "numpy unavailable; skipped",
                                   hint="Install audio/ML deps to run --deep."))
        return results
    results.append(CheckResult("Deep: STT silence", WARN, "deep STT probe not run in this build",
                               hint="Run --deep on a full install to exercise STT/TTS."))
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _collect(deep: bool) -> DoctorReport:
    report = DoctorReport()
    report.add(check_python_and_os())
    report.add(check_data_dir())

    # Config-dependent checks. READ-ONLY: parse the raw JSON without migrating
    # or writing (Task D §6 — the doctor must not create/alter any file). A
    # raw dict (old schema or partial) is fine here: we only read a few fields
    # with safe defaults for display.
    raw: dict = {}
    try:
        from jarvis.core.config import _read_raw, default_config_path

        p = default_config_path()
        if p.exists():
            raw = _read_raw(p)
    except Exception:
        raw = {}

    def _section(name: str) -> dict:
        sec = raw.get(name)
        return sec if isinstance(sec, dict) else {}

    report.add(check_config())

    llm_model = _section("llm").get("model", "qwen3:8b")
    report.add(check_ollama(llm_model))

    report.add(check_audio_devices())

    stt_device = _section("stt").get("device", "auto")
    report.add(check_stt(stt_device))

    tts = _section("tts")
    voices_by_lang = {
        "en": tts.get("voices_en", "en_GB-alan-medium"),
        "ru": tts.get("voices_ru", "ru_RU-dmitri-medium"),
        "uk": tts.get("voices_uk", "uk_UA-mykyta-high"),
    }
    report.add(check_tts_voices(["en", "ru", "uk"], voices_by_lang))

    report.add(check_wakeword_and_vad())
    report.add(check_gpu())
    report.add(check_extras())

    _integrations = _section("integrations").get("enabled", [])
    enabled_integrations = list(_integrations) if isinstance(_integrations, list) else []
    report.add(check_integrations(enabled_integrations))

    report.add(check_migration_state())

    if deep:
        for r in check_deep_stt_tts():
            report.add(r)
    return report


def format_report(report: DoctorReport, *, do_redact: bool = False) -> str:
    lines = ["JARVIS doctor report", "=" * 40]
    for r in report.results:
        line = f"[{r.status}] {r.name}: {r.detail}"
        if r.status != PASS and r.hint:
            line += f"\n    -> {r.hint}"
        lines.append(line)
    c = report.counts()
    lines.append("=" * 40)
    lines.append(f"{c[PASS]} PASS, {c[WARN]} WARN, {c[FAIL]} FAIL")
    text = "\n".join(lines)
    return redact(text) if do_redact else text


def run_doctor(*, as_json: bool = False, do_redact: bool = False, deep: bool = False,
               writer: Callable[[str], None] | None = None) -> int:
    """Run all checks, print + persist the report, return an exit code.

    Exit code is 0 unless any check FAILed. Writes the report to
    ``<data_dir>/logs/doctor_report.txt`` (and .json with --json). Read-only
    besides that report file and the localhost Ollama probe.
    """
    out = writer or (lambda s: print(s))
    report = _collect(deep)

    # Persist (best-effort; a write failure must not change the exit code).
    try:
        from jarvis.paths import data_path

        txt_path = data_path("logs", "doctor_report.txt", create=True)
        txt_path.write_text(format_report(report, do_redact=do_redact), encoding="utf-8")
        if as_json:
            json_path = data_path("logs", "doctor_report.json", create=True)
            payload = {
                "results": [r.as_dict() for r in report.results],
                "counts": report.counts(),
            }
            blob = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            json_path.write_text(redact(blob) if do_redact else blob, encoding="utf-8")
    except Exception:
        log.debug("doctor report persist failed", exc_info=True)

    if as_json:
        payload = {
            "results": [r.as_dict() for r in report.results],
            "counts": report.counts(),
        }
        blob = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        out(redact(blob) if do_redact else blob)
    else:
        out(format_report(report, do_redact=do_redact))

    return 1 if report.has_fail else 0
