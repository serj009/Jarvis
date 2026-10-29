"""Tests for T1.5: instance_lock, ollama_check, and error handler improvements."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 0. Syntax / import smoke tests
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent

def test_instance_lock_parses():
    src = (REPO / "jarvis" / "core" / "instance_lock.py").read_text(encoding="utf-8")
    ast.parse(src)

def test_ollama_check_parses():
    src = (REPO / "jarvis" / "core" / "ollama_check.py").read_text(encoding="utf-8")
    ast.parse(src)

def test_app_parses():
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    ast.parse(src)

def test_pipeline_parses():
    src = (REPO / "jarvis" / "audio" / "pipeline.py").read_text(encoding="utf-8")
    ast.parse(src)


# ---------------------------------------------------------------------------
# 1. instance_lock tests
# ---------------------------------------------------------------------------

def test_instance_lock_acquire_release():
    """Lock can be acquired and released cleanly."""
    from jarvis.core.instance_lock import acquire_instance_lock, release_instance_lock

    lock = acquire_instance_lock()
    assert lock is not None, "Should acquire lock on first attempt"
    release_instance_lock(lock)


def test_instance_lock_double_acquire():
    """Second acquire returns None while first lock is held."""
    from jarvis.core.instance_lock import acquire_instance_lock, release_instance_lock

    lock1 = acquire_instance_lock()
    assert lock1 is not None

    try:
        lock2 = acquire_instance_lock()
        assert lock2 is None, "Should fail to acquire while first lock is held"
    finally:
        release_instance_lock(lock1)


def test_instance_lock_release_none():
    """release_instance_lock(None) is a safe no-op."""
    from jarvis.core.instance_lock import release_instance_lock
    release_instance_lock(None)  # should not raise


# ---------------------------------------------------------------------------
# 2. ollama_check tests
# ---------------------------------------------------------------------------

def test_ollama_check_result_fields():
    """OllamaCheckResult has expected fields."""
    from jarvis.core.ollama_check import OllamaCheckResult

    result = OllamaCheckResult(ok=True, warning="", detail="")
    assert result.ok is True
    assert result.warning == ""
    assert result.auto_started is False


def test_ollama_check_ping_failure():
    """When Ollama is unreachable, check returns ok=False with warning."""
    from jarvis.core.ollama_check import check_ollama

    # Use a port that's almost certainly not listening
    result = check_ollama(endpoint="http://127.0.0.1:19999")
    assert result.ok is False
    assert "Ollama is not running" in result.warning
    assert result.detail != ""


def test_ollama_check_ping_success():
    """When _ping succeeds, check returns ok=True."""
    from jarvis.core.ollama_check import check_ollama

    with patch("jarvis.core.ollama_check._ping", return_value=True):
        result = check_ollama()
    assert result.ok is True
    assert result.warning == ""


# ---------------------------------------------------------------------------
# 3. app.py integration structure tests
# ---------------------------------------------------------------------------

def test_start_has_instance_lock():
    """start() method contains acquire_instance_lock call."""
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    assert "acquire_instance_lock()" in src
    assert "release_instance_lock(self._instance_lock)" in src


def test_start_has_ollama_check():
    """start() method calls check_ollama()."""
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    assert "check_ollama()" in src
    assert "_preflight_ollama_warning" in src


def test_start_order():
    """Instance lock precedes config load, which precedes Ollama check."""
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    idx_lock = src.index("acquire_instance_lock()")
    idx_config = src.index("self._load_config()")
    idx_ollama = src.index("check_ollama()")
    idx_audio = src.index("self._build_audio_stack()")

    assert idx_lock < idx_config < idx_ollama < idx_audio, (
        "Order must be: lock → config → ollama_check → build_audio_stack"
    )


def test_finally_releases_lock():
    """exec() is wrapped in try/finally that releases the lock."""
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    # The pattern: try: ... exec() ... finally: ... release_instance_lock
    idx_exec = src.index("self.qt_app.exec()")
    idx_release = src.index("release_instance_lock(self._instance_lock)")
    assert idx_release > idx_exec


# ---------------------------------------------------------------------------
# 4. pipeline error handler tests
# ---------------------------------------------------------------------------

def test_pipeline_speaks_error_on_crash():
    """Response chain exception handler speaks error_generic via TTS."""
    src = (REPO / "jarvis" / "audio" / "pipeline.py").read_text(encoding="utf-8")
    assert 'say("error_generic")' in src
    assert "response chain crashed" in src
    # Verify TTS speak is called in the except block
    assert "await self._tts.speak(say(" in src


def test_pipeline_recovers_to_idle():
    """Exception handler resets state to IDLE."""
    src = (REPO / "jarvis" / "audio" / "pipeline.py").read_text(encoding="utf-8")
    assert "set_conversational_state(ConversationalState.IDLE)" in src


# ---------------------------------------------------------------------------
# 5. Error hierarchy tests
# ---------------------------------------------------------------------------

def test_error_handler_exists():
    """ErrorHandler with safe_call and FAILED sentinel exists."""
    from jarvis.core.errors import ErrorHandler

    handler = ErrorHandler(bus=None)
    assert hasattr(handler, "safe_call")
    assert hasattr(handler, "FAILED")
    assert repr(handler.FAILED) == "<FAILED>"


def test_error_handler_spoken_message():
    """spoken_message returns human-readable text for error keys."""
    from jarvis.core.errors import ErrorHandler

    handler = ErrorHandler(bus=None)
    msg = handler.spoken_message("error_generic")
    assert "wrong" in msg.lower() or "не так" in msg.lower() or "пішло" in msg.lower()


def test_boot_error_holder_ollama_warning():
    """_boot_audio_stack handles ollama_warning: prefix in boot_error_holder."""
    src = (REPO / "jarvis" / "app.py").read_text(encoding="utf-8")
    assert 'error.startswith("ollama_warning:")' in src
    assert "Jarvis — Ollama Not Running" in src
