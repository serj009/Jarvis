"""Tests for jarvis.core.doctor (Task C part 2).

All external calls (Ollama HTTP, nvidia-smi, sounddevice, setup_jarvis hashes)
are mocked, so the doctor is exercised on both an empty and a healthy
environment without touching real hardware or the network.

jarvis.core.doctor imports cleanly without audio/Qt deps (checks import their
heavy modules lazily inside try/except), so this file is NOT auto-skipped in a
lightweight venv.

JARVIS_DATA_DIR is repointed to a temp dir by the autouse conftest fixture.
"""

from __future__ import annotations

import json

from jarvis.core import doctor
from jarvis.core.doctor import (
    FAIL,
    PASS,
    WARN,
    CheckResult,
    DoctorReport,
    check_config,
    check_data_dir,
    check_extras,
    check_integrations,
    check_ollama,
    check_python_and_os,
    format_report,
    redact,
    run_doctor,
)

# ---------------------------------------------------------------------------
# simple checks
# ---------------------------------------------------------------------------


def test_python_and_os_passes_on_supported():
    r = check_python_and_os()
    assert r.status == PASS  # test runs on >= 3.11


def test_python_fails_when_too_old(monkeypatch):
    class V:
        major, minor, micro = 3, 10, 0

    monkeypatch.setattr(doctor.sys, "version_info", V)
    r = check_python_and_os()
    assert r.status == FAIL
    assert "3.11" in r.hint


def test_data_dir_pass_in_temp_with_env(tmp_path):
    # conftest set JARVIS_DATA_DIR to a temp dir, but since it IS set the temp
    # location does not warn (per the log_data_dir_choice rule).
    r = check_data_dir()
    assert r.status in (PASS, WARN)  # PASS normally; WARN only on low disk
    assert r.status != FAIL


def test_config_absent_is_warn(monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "config.json"
    import jarvis.core.config as cfg

    monkeypatch.setattr(cfg, "default_config_path", lambda: missing)
    r = check_config()
    assert r.status == WARN


# ---------------------------------------------------------------------------
# ollama (mocked HTTP)
# ---------------------------------------------------------------------------


def test_ollama_unreachable_is_fail(monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_tags", lambda endpoint=None: None)
    r = check_ollama("qwen3:8b")
    assert r.status == FAIL
    assert "not reachable" in r.detail


def test_ollama_model_missing_is_fail(monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_tags", lambda endpoint=None: ["llama3:8b"])
    r = check_ollama("qwen3:8b")
    assert r.status == FAIL
    assert "pull" in r.hint


def test_ollama_model_present_is_pass(monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_tags", lambda endpoint=None: ["qwen3:8b", "x"])
    r = check_ollama("qwen3:8b")
    assert r.status == PASS


def test_ollama_matches_on_base_name(monkeypatch):
    # "qwen3:8b" configured, tag "qwen3:latest" — base name matches.
    monkeypatch.setattr(doctor, "_ollama_tags", lambda endpoint=None: ["qwen3:latest"])
    r = check_ollama("qwen3:8b")
    assert r.status == PASS


# ---------------------------------------------------------------------------
# integrations: never print token values
# ---------------------------------------------------------------------------


def test_integrations_none_enabled_is_pass():
    r = check_integrations([])
    assert r.status == PASS
    assert "none" in r.detail.lower()


def test_integrations_missing_token_warns(monkeypatch):
    class FakeAuth:
        def has_credentials(self, name):
            return name == "google"

    monkeypatch.setattr(
        "jarvis.integrations.auth.IntegrationAuth", lambda *a, **k: FakeAuth()
    )
    r = check_integrations(["google", "slack"])
    assert r.status == WARN
    assert "google=token ok" in r.detail
    assert "slack=NO TOKEN" in r.detail
    # The actual secret value must never be present (we only ever print a flag).
    assert "token ok" in r.detail and "sk-" not in r.detail


# ---------------------------------------------------------------------------
# extras (never fail)
# ---------------------------------------------------------------------------


def test_extras_never_fails():
    # With no NVIDIA GPU present, extras are purely informational → PASS,
    # regardless of which optional packages are installed. Mock the GPU probe
    # so this is hardware-independent.
    monkeypatch_mod = __import__("unittest.mock", fromlist=["patch"])
    with monkeypatch_mod.patch(
        "jarvis.core.doctor._nvidia_gpu_present", return_value=False
    ):
        r = check_extras()
    assert r.status == PASS
    assert "installed:" in r.detail


def test_extras_warns_when_gpu_absent_but_nvidia_present(monkeypatch):
    """gpu extra missing + an NVIDIA card present → WARN with install hint."""
    import importlib.util as _ilu

    real_find_spec = _ilu.find_spec

    def fake_find_spec(name, *a, **k):
        if name == "nvidia.cublas":
            return None  # pretend the gpu runtime wheels are not installed
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(_ilu, "find_spec", fake_find_spec)
    monkeypatch.setattr(doctor, "_nvidia_gpu_present", lambda: True)

    r = check_extras()
    assert r.status == WARN
    assert "gpu" in r.detail
    assert ".[gpu]" in r.hint


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_removes_home_and_user(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\Users\bob")))
    monkeypatch.setenv("USERNAME", "bob")
    text = r"data dir: C:\Users\bob\AppData\Roaming\Jarvis (user bob)"
    out = redact(text)
    assert r"C:\Users\bob" not in out
    assert "<user>" in out or "~" in out


# ---------------------------------------------------------------------------
# orchestration: exit code + report persistence + json
# ---------------------------------------------------------------------------


def _stub_collect(report: DoctorReport):
    def _c(deep: bool):
        return report
    return _c


def test_run_doctor_exit_zero_when_no_fail(monkeypatch, capsys):
    rep = DoctorReport()
    rep.add(CheckResult("A", PASS, "ok"))
    rep.add(CheckResult("B", WARN, "meh", hint="do x"))
    monkeypatch.setattr(doctor, "_collect", _stub_collect(rep))

    code = run_doctor()
    assert code == 0
    out = capsys.readouterr().out
    assert "1 PASS, 1 WARN, 0 FAIL" in out


def test_run_doctor_exit_one_when_fail(monkeypatch, capsys):
    rep = DoctorReport()
    rep.add(CheckResult("A", FAIL, "broken", hint="fix it"))
    monkeypatch.setattr(doctor, "_collect", _stub_collect(rep))

    code = run_doctor()
    assert code == 1


def test_run_doctor_writes_report_file(monkeypatch):
    from jarvis.paths import data_path

    rep = DoctorReport()
    rep.add(CheckResult("A", PASS, "ok"))
    monkeypatch.setattr(doctor, "_collect", _stub_collect(rep))

    run_doctor()
    report_file = data_path("logs", "doctor_report.txt")
    assert report_file.exists()
    assert "JARVIS doctor report" in report_file.read_text(encoding="utf-8")


def test_run_doctor_json_output_and_file(monkeypatch, capsys):
    from jarvis.paths import data_path

    rep = DoctorReport()
    rep.add(CheckResult("A", PASS, "ok"))
    monkeypatch.setattr(doctor, "_collect", _stub_collect(rep))

    code = run_doctor(as_json=True)
    assert code == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["counts"]["PASS"] == 1
    assert (data_path("logs", "doctor_report.json")).exists()


def test_run_doctor_redacts(monkeypatch, capsys):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\Users\bob")))
    rep = DoctorReport()
    rep.add(CheckResult("Data", PASS, r"C:\Users\bob\AppData\Roaming\Jarvis"))
    monkeypatch.setattr(doctor, "_collect", _stub_collect(rep))

    run_doctor(do_redact=True)
    out = capsys.readouterr().out
    assert r"C:\Users\bob" not in out


def test_format_report_shows_hints_only_for_non_pass():
    rep = DoctorReport()
    rep.add(CheckResult("Good", PASS, "fine", hint="SHOULD-NOT-SHOW"))
    rep.add(CheckResult("Bad", FAIL, "broke", hint="SHOULD-SHOW"))
    text = format_report(rep)
    assert "SHOULD-SHOW" in text
    assert "SHOULD-NOT-SHOW" not in text


# ---------------------------------------------------------------------------
# Task 2: doctor tells the truth (STT CUDA, Silero runtime path, migration list)
# ---------------------------------------------------------------------------


def test_stt_warns_when_cuda_libraries_fail(monkeypatch):
    """configured=cuda but the CUDA DLL does not load → WARN, not PASS."""
    # Resolve to cuda, model present, but the probe reports a load failure.
    import jarvis.audio.stt as stt_mod
    monkeypatch.setattr(stt_mod.FasterWhisperSTT, "_resolve_device",
                        staticmethod(lambda d: "cuda"))
    import jarvis.paths as paths_mod
    monkeypatch.setattr(paths_mod, "default_whisper_download_root",
                        lambda: None)  # model not downloaded is irrelevant here
    import jarvis.platform.cuda_dlls as cd
    monkeypatch.setattr(cd, "probe_cuda_libraries",
                        lambda: (False, "cublas64_12.dll failed to load (x)"))

    r = doctor.check_stt("cuda")
    assert r.status == WARN
    assert "DO NOT load" in r.detail
    assert "cublas" in r.detail.lower()


def test_stt_pass_when_cuda_loads(monkeypatch):
    import jarvis.audio.stt as stt_mod
    monkeypatch.setattr(stt_mod.FasterWhisperSTT, "_resolve_device",
                        staticmethod(lambda d: "cuda"))
    import jarvis.paths as paths_mod
    monkeypatch.setattr(paths_mod, "default_whisper_download_root",
                        lambda: paths_mod.user_data_dir())  # pretend present dir
    import jarvis.platform.cuda_dlls as cd
    monkeypatch.setattr(cd, "probe_cuda_libraries",
                        lambda: (True, "cublas64_12.dll loaded"))

    r = doctor.check_stt("cuda")
    assert r.status == PASS
    assert "CUDA libraries load OK" in r.detail


def test_wakeword_vad_uses_runtime_silero(monkeypatch, tmp_path):
    """Silero found via the runtime site-packages resolver (vad._find_silero_onnx)."""
    onnx = tmp_path / "silero_vad.onnx"
    onnx.write_bytes(b"x")
    import jarvis.audio.vad as vad_mod
    monkeypatch.setattr(vad_mod, "_find_silero_onnx", lambda: onnx)

    import importlib.util as _ilu
    real = _ilu.find_spec
    monkeypatch.setattr(
        _ilu, "find_spec",
        lambda n, *a, **k: object() if n == "openwakeword" else real(n, *a, **k),
    )
    r = doctor.check_wakeword_and_vad()
    assert r.status == PASS
    assert "silero_vad.onnx" in r.detail


def test_migration_lists_pending_paths(monkeypatch, tmp_path):
    """Legacy ~/.jarvis with un-migrated files → WARN listing relative paths."""
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    legacy = tmp_path / "dotjarvis"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "memory" / "memory.db").write_text("x", encoding="utf-8")
    new_root = tmp_path / "appdata"
    new_root.mkdir()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(new_root))
    import jarvis.core.data_migration as dm
    monkeypatch.setattr(dm, "_legacy_root", lambda: legacy)

    r = doctor.check_migration_state()
    assert r.status == WARN
    assert "config.json" in r.detail
    assert "memory/memory.db" in r.detail
