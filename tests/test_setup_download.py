"""Tests for setup_jarvis._download_file integrity verification.

Covers the SHA-256 gate added in Этап 4:
  * a download whose bytes don't match the expected hash is rejected and
    the temp file is cleaned up (no partial/unverified file is left);
  * a matching hash commits the file atomically;
  * a missing expected hash (None) is a hard failure — we never install a
    file we cannot verify.

The network is mocked: ``urllib.request.urlopen`` is replaced with a fake
response that streams canned bytes, so no real HuggingFace fetch happens.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

import setup_jarvis


# ---------------------------------------------------------------------------
# Fake urlopen
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal context-manager stand-in for an http response."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def _fake_urlopen(payload: bytes):
    def _opener(req, timeout=0):  # noqa: ARG001
        return _FakeResponse(payload)
    return _opener


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_download_rejects_hash_mismatch(tmp_path: Path) -> None:
    payload = b"malicious-substituted-bytes"
    dest = tmp_path / "voice.onnx"
    wrong_hash = "0" * 64  # definitely not the payload's digest

    with patch(
        "setup_jarvis.urllib.request.urlopen",
        _fake_urlopen(payload),
    ):
        ok = setup_jarvis._download_file(
            "https://example/voice.onnx", dest, "voice.onnx",
            expected_sha256=wrong_hash,
        )

    assert ok is False
    # The real file must NOT exist…
    assert not dest.exists()
    # …and no leftover temp file in the directory.
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_download_accepts_matching_hash(tmp_path: Path) -> None:
    payload = b"legit piper voice model bytes"
    dest = tmp_path / "voice.onnx"
    good_hash = hashlib.sha256(payload).hexdigest()

    with patch(
        "setup_jarvis.urllib.request.urlopen",
        _fake_urlopen(payload),
    ):
        ok = setup_jarvis._download_file(
            "https://example/voice.onnx", dest, "voice.onnx",
            expected_sha256=good_hash,
        )

    assert ok is True
    assert dest.exists()
    assert dest.read_bytes() == payload
    # No temp .part files left over.
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == ["voice.onnx"], f"unexpected files: {leftovers}"


def test_download_refuses_without_expected_hash(tmp_path: Path) -> None:
    """None expected hash is fail-closed: no network call, no file."""
    dest = tmp_path / "voice.onnx"

    called = {"urlopen": False}

    def _should_not_open(req, timeout=0):  # noqa: ARG001
        called["urlopen"] = True
        raise AssertionError("urlopen must not be called without a hash")

    with patch("setup_jarvis.urllib.request.urlopen", _should_not_open):
        ok = setup_jarvis._download_file(
            "https://example/voice.onnx", dest, "voice.onnx",
            expected_sha256=None,
        )

    assert ok is False
    assert not dest.exists()
    assert called["urlopen"] is False


def test_download_case_insensitive_hash(tmp_path: Path) -> None:
    """Expected hash in upper-case still matches (hexdigest is lower)."""
    payload = b"some bytes"
    dest = tmp_path / "f.onnx"
    good_hash = hashlib.sha256(payload).hexdigest().upper()

    with patch(
        "setup_jarvis.urllib.request.urlopen",
        _fake_urlopen(payload),
    ):
        ok = setup_jarvis._download_file(
            "https://example/f.onnx", dest, "f.onnx",
            expected_sha256=good_hash,
        )

    assert ok is True
    assert dest.read_bytes() == payload


def test_piper_voices_builds_pinned_urls() -> None:
    """_piper_voices injects the commit into every URL (no resolve/main)."""
    voices = setup_jarvis._piper_voices("deadbeef" * 5)  # 40 hex chars
    assert voices, "expected at least one voice"
    for _name, url in voices:
        assert "resolve/main" not in url
        assert "resolve/" + ("deadbeef" * 5) in url
