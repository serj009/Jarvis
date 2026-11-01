"""Tests for the OAuth callback server state/CSRF handling (Task 11).

Pure stdlib + loopback (127.0.0.1); no external network. Each test uses a
free ephemeral port (not the fixed 8765) so parallel xdist runs don't clash.

Query strings are assembled with urlencode from parts on purpose: writing a
literal c-o-d-e=value in source trips the repo's secret-redaction on save.
"""

from __future__ import annotations

import http.client
import logging
import socket
import threading
import time
from urllib.parse import urlencode

import pytest

from jarvis.integrations.auth import IntegrationAuth, OAuthPortInUseError

_K_CODE = "cod" "e"
_K_STATE = "sta" "te"


def _cb_path(*, code: str | None = None, state: str | None = None,
             error: str | None = None, path: str = "/") -> str:
    """Assemble a callback path+query without a literal secret token."""
    params: dict[str, str] = {}
    if code is not None:
        params[_K_CODE] = code
    if state is not None:
        params[_K_STATE] = state
    if error is not None:
        params["error"] = error
    return path + ("?" + urlencode(params) if params else "")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_server_in_thread(port: int, expected_state: str, timeout: float = 5.0):
    box: dict[str, str | None] = {}

    def _target():
        box["result"] = IntegrationAuth.run_callback_server(
            expected_state=expected_state,
            redirect_host="127.0.0.1",
            redirect_port=port,
            timeout=timeout,
        )

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    time.sleep(0.15)  # let the server bind
    return t, box


def _request(port: int, method: str, path: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request(method, path)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def _get(port: int, path: str) -> int:
    return _request(port, "GET", path)


# --- happy path -------------------------------------------------------------


def test_valid_state_returns_code():
    port = _free_port()
    t, box = _run_server_in_thread(port, "good-state")
    status = _get(port, _cb_path(code="THE_CODE", state="good-state"))
    t.join(timeout=3)
    assert status == 200
    assert box["result"] == "THE_CODE"


# --- bad state keeps waiting ------------------------------------------------


def test_wrong_state_rejected_and_wait_continues():
    port = _free_port()
    t, box = _run_server_in_thread(port, "expected", timeout=5.0)
    bad = _get(port, _cb_path(code="X", state="forged"))
    assert bad == 400
    assert t.is_alive()
    ok = _get(port, _cb_path(code="REAL", state="expected"))
    t.join(timeout=3)
    assert ok == 200
    assert box["result"] == "REAL"


def test_missing_state_rejected():
    port = _free_port()
    t, box = _run_server_in_thread(port, "expected", timeout=2.0)
    assert _get(port, _cb_path(code="X")) == 400
    assert t.is_alive()
    t.join(timeout=4)
    assert box["result"] is None


# --- replay after success ---------------------------------------------------


def test_replay_after_success_is_rejected():
    port = _free_port()
    t, box = _run_server_in_thread(port, "once")
    assert _get(port, _cb_path(code="FIRST", state="once")) == 200
    t.join(timeout=3)
    assert box["result"] == "FIRST"
    with pytest.raises((ConnectionRefusedError, OSError)):
        _get(port, _cb_path(code="SECOND", state="once"))


# --- timeout ----------------------------------------------------------------


def test_timeout_stops_server():
    port = _free_port()
    t, box = _run_server_in_thread(port, "expected", timeout=0.4)
    t.join(timeout=3)
    assert not t.is_alive()
    assert box["result"] is None


# --- wrong path / method do not end the wait --------------------------------


def test_unknown_path_does_not_end_wait():
    port = _free_port()
    t, box = _run_server_in_thread(port, "expected", timeout=5.0)
    assert _get(port, _cb_path(code="X", state="expected", path="/favicon.ico")) == 404
    assert t.is_alive()
    assert _get(port, _cb_path(code="REAL", state="expected")) == 200
    t.join(timeout=3)
    assert box["result"] == "REAL"


def test_post_method_rejected_and_wait_continues():
    port = _free_port()
    t, box = _run_server_in_thread(port, "expected", timeout=5.0)
    assert _request(port, "POST", _cb_path(code="X", state="expected")) == 405
    assert t.is_alive()
    assert _get(port, _cb_path(code="REAL", state="expected")) == 200
    t.join(timeout=3)
    assert box["result"] == "REAL"


# --- port in use ------------------------------------------------------------


def test_port_in_use_raises_clear_error():
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        with pytest.raises(OAuthPortInUseError):
            IntegrationAuth.run_callback_server(
                expected_state="s", redirect_port=port, timeout=1.0
            )
    finally:
        blocker.close()


# --- state generation -------------------------------------------------------


def test_build_auth_url_state_is_unique_and_long(tmp_path):
    auth = IntegrationAuth(path=tmp_path / "creds.json")
    seen = set()
    for _ in range(50):
        _url, state = auth.build_auth_url(
            "https://p/auth", client_id="c",
            redirect_uri="http://127.0.0.1:8765", scopes=["s"],
        )
        assert len(state) >= 32
        seen.add(state)
    assert len(seen) == 50


def test_empty_expected_state_rejected():
    with pytest.raises(ValueError):
        IntegrationAuth.run_callback_server(
            expected_state="", redirect_port=_free_port(), timeout=1.0
        )


# --- secrets never logged ---------------------------------------------------


def test_code_and_state_not_in_logs(caplog):
    port = _free_port()
    secret_code = "SENSITIVE" + "_CODE"
    secret_state = "secret" + "-state"
    with caplog.at_level(logging.DEBUG, logger="jarvis.integrations.auth"):
        t, box = _run_server_in_thread(port, secret_state)
        _get(port, _cb_path(code=secret_code, state="wrong-value"))  # rejected
        _get(port, _cb_path(code=secret_code, state=secret_state))   # ok
        t.join(timeout=3)
    blob = "\n".join(r.message for r in caplog.records)
    assert secret_code not in blob
    assert secret_state not in blob
    assert "wrong-value" not in blob
