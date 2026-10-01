"""Smoke-test for a live Jarvis install (roadmap T1.6).

Quick end-to-end checks of the real environment -- not unit tests:

  1. config.json loads and validates        (critical)
  2. Ollama API answers                      (critical)
  3. The configured LLM model is installed
  4. The model answers a minimal request, timed
  5. Whisper (STT) loads and handles silence
  6. The configured Piper voice loads (TTS)
  7. GPU memory via nvidia-smi
  8. logs/ is writable
  9. The autostart task is registered

Read-only by design: config.json is never created or rewritten (load_config
would write a default file if it were missing, so existence is checked
first), and the Ollama request uses the same num_ctx / think settings as
Jarvis so it does not force a model reload.

Run from the project root (Jarvis may keep running):

    .\\.venv\\Scripts\\python.exe -m jarvis.dev.smoke_test
    .\\.venv\\Scripts\\python.exe -m jarvis.dev.smoke_test --repeat 5

Exit code: 0 when nothing FAILed (WARN does not fail the run), 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OLLAMA_API = "http://127.0.0.1:11434"
TASK_NAME = "JarvisAutostart"
# Roadmap T0.1: the model should answer a test question in under 10 s.
LLM_ANSWER_BUDGET_S = 10.0
# Warn when the GPU is almost full: Ollama would start spilling to RAM.
VRAM_WARN_FRACTION = 0.92

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class CheckFailed(Exception):
    """A check found a real problem."""


class CheckWarning(Exception):
    """A check found something worth a look, but not a failure."""


@dataclass
class Context:
    """Values shared between checks (filled in as checks pass)."""

    cfg: Any = None
    installed_models: list[str] = field(default_factory=list)


@dataclass
class Result:
    name: str
    status: str
    detail: str
    ms: float


# --- checks -----------------------------------------------------------------
# Each returns a short detail string on success, raises CheckWarning /
# CheckFailed otherwise.


def check_config(ctx: Context) -> str:
    from jarvis.core.config import (
        CONFIG_LOAD_ERRORS,
        default_config_path,
        format_config_error,
        load_config,
    )

    path = default_config_path()
    if not path.exists():
        raise CheckFailed(f"not found: {path}")
    try:
        ctx.cfg = load_config(path)
    except CONFIG_LOAD_ERRORS as exc:
        raise CheckFailed(format_config_error(exc, path)) from None
    return str(path)


def _get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def check_ollama_api(ctx: Context) -> str:
    try:
        data = _get_json(f"{OLLAMA_API}/api/tags", timeout=5)
    except (urllib.error.URLError, OSError) as exc:
        raise CheckFailed(f"{OLLAMA_API} not reachable: {exc}") from None
    ctx.installed_models = [m.get("name", "") for m in data.get("models", [])]
    return f"{len(ctx.installed_models)} model(s) installed"


def check_model_installed(ctx: Context) -> str:
    want = ctx.cfg.llm.model
    names = set(ctx.installed_models)
    if want in names or f"{want}:latest" in names:
        return want
    raise CheckFailed(
        f"'{want}' (llm.model in config.json) is not installed; "
        f"installed: {', '.join(sorted(names)) or 'none'}"
    )


def check_model_answers(ctx: Context) -> str:
    # Same request shape as OllamaClient.warm(): identical num_ctx/think so
    # the already-loaded model is reused instead of reloaded.
    from jarvis.llm.ollama_client import DEFAULT_NUM_CTX, DEFAULT_THINK

    body = json.dumps({
        "model": ctx.cfg.llm.model,
        "messages": [{"role": "user", "content": "ready"}],
        "stream": False,
        "think": DEFAULT_THINK,
        "keep_alive": f"{ctx.cfg.llm.keep_alive_seconds}s",
        "options": {"num_predict": 1, "num_ctx": DEFAULT_NUM_CTX},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_API}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            json.loads(resp.read())
    except (urllib.error.URLError, OSError) as exc:
        raise CheckFailed(f"request failed: {exc}") from None
    elapsed = time.monotonic() - start
    detail = f"answered in {elapsed:.1f} s (num_ctx={DEFAULT_NUM_CTX}, think={DEFAULT_THINK})"
    if elapsed > LLM_ANSWER_BUDGET_S:
        raise CheckWarning(detail + f"; budget {LLM_ANSWER_BUDGET_S:.0f} s (cold load?)")
    return detail


def check_whisper(ctx: Context) -> str:
    from jarvis.audio.stt import FasterWhisperSTT
    from jarvis.paths import default_whisper_download_root

    stt_cfg = ctx.cfg.stt
    stt = FasterWhisperSTT(
        model_size=stt_cfg.model_size,
        language=stt_cfg.language,
        compute_type=stt_cfg.compute_type,
        download_root=default_whisper_download_root(),
    )

    async def run() -> str:
        await stt.load()
        try:
            # 1 s of silence, int16 mono @ 16 kHz: must come back empty.
            return await stt.transcribe(b"\x00\x00" * 16000)
        finally:
            await stt.unload()

    text = asyncio.run(run())
    detail = f"model={stt_cfg.model_size}, language={stt_cfg.language}"
    if text:
        raise CheckWarning(detail + f"; silence was transcribed as {len(text)} chars")
    return detail


def check_piper_voice(ctx: Context) -> str:
    from jarvis.paths import default_voices_dir

    voice = ctx.cfg.tts.voice
    voices_dir = default_voices_dir()
    onnx = voices_dir / f"{voice}.onnx"
    meta = voices_dir / f"{voice}.onnx.json"
    missing = [p.name for p in (onnx, meta) if not p.is_file()]
    if missing:
        raise CheckFailed(f"missing in {voices_dir}: {', '.join(missing)}")
    from piper import PiperVoice

    PiperVoice.load(str(onnx))
    return f"{voice} ({voices_dir})"


def check_vram(ctx: Context) -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckWarning(f"nvidia-smi unavailable: {exc}") from None
    used, total = (int(x) for x in out.strip().splitlines()[0].split(","))
    detail = f"{used} / {total} MiB used"
    if used > total * VRAM_WARN_FRACTION:
        raise CheckWarning(detail + " (almost full)")
    return detail


def check_logs_writable(ctx: Context) -> str:
    from jarvis.paths import install_root

    logs = install_root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    probe = logs / ".smoke_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return str(logs)


def check_autostart_task(ctx: Context) -> str:
    if sys.platform != "win32":
        raise CheckWarning("not Windows; skipped")
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise CheckWarning(f"task '{TASK_NAME}' not registered (run register-task.ps1)")
    return f"task '{TASK_NAME}' registered"


# (label, function, critical). A failed critical check stops the run:
# everything after it depends on it.
CHECKS: list[tuple[str, Callable[[Context], str], bool]] = [
    ("config.json", check_config, True),
    ("Ollama API", check_ollama_api, True),
    ("LLM model installed", check_model_installed, False),
    ("LLM answers", check_model_answers, False),
    ("Whisper STT", check_whisper, False),
    ("Piper voice", check_piper_voice, False),
    ("GPU memory", check_vram, False),
    ("logs/ writable", check_logs_writable, False),
    ("Autostart task", check_autostart_task, False),
]


# --- runner -----------------------------------------------------------------


def run_once() -> list[Result]:
    ctx = Context()
    results: list[Result] = []
    for name, fn, critical in CHECKS:
        start = time.monotonic()
        try:
            status, detail = PASS, fn(ctx)
        except CheckWarning as exc:
            status, detail = WARN, str(exc)
        except CheckFailed as exc:
            status, detail = FAIL, str(exc)
        except Exception as exc:  # unexpected: report, keep going
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"
        ms = (time.monotonic() - start) * 1000
        results.append(Result(name, status, detail, ms))
        print(f"  [{status}] {name:<20} {ms:7.0f} ms  {detail}", flush=True)
        if status == FAIL and critical:
            print("  critical check failed; remaining checks skipped", flush=True)
            break
    return results


def main(argv: list[str] | None = None) -> int:
    # Never crash on a console that cannot print some character.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=1, help="run N times in a row")
    args = parser.parse_args(argv)

    failed_runs = 0
    for i in range(1, max(1, args.repeat) + 1):
        print(f"\nJarvis smoke-test, run {i}/{args.repeat}")
        results = run_once()
        fails = sum(r.status == FAIL for r in results)
        warns = sum(r.status == WARN for r in results)
        passes = sum(r.status == PASS for r in results)
        print(f"  => {passes} pass, {warns} warn, {fails} fail")
        failed_runs += fails > 0

    if args.repeat > 1:
        print(f"\n{args.repeat - failed_runs}/{args.repeat} runs without failures")
    return 1 if failed_runs else 0


if __name__ == "__main__":
    raise SystemExit(main())
