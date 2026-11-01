"""Credential storage + OAuth2 helper for online integrations.

Credentials (API keys, OAuth tokens) are stored with ``EncryptedJsonStore``
(Windows DPAPI at rest). We deliberately do NOT use the crypto PasswordVault
here: the vault needs an interactive master password to unlock, which a
background integration loading tokens at startup cannot supply. DPAPI gives
the same at-rest protection, scoped to the Windows user, with no prompt.

OAuth2 uses only the standard library (a transient localhost callback
server) plus ``httpx`` (already a core dependency) for the token exchange —
zero new pip dependencies.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from pathlib import Path

from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)


class OAuthPortInUseError(RuntimeError):
    """The OAuth callback port is already bound by another process.

    Raised by run_callback_server instead of hanging or leaking a cryptic
    OSError, so the UI can tell the user exactly what to do.
    """


# Length of the CSRF state token, in url-safe characters (Task 11).
_STATE_NBYTES = 32


def _creds_path() -> Path:
    # Thin wrapper over the single data-dir resolver (jarvis.paths); layout
    # unchanged: <data_dir>/integration_credentials.json. Resolved at call time.
    from jarvis.paths import data_path

    return data_path("integration_credentials.json")


class IntegrationAuth:
    """Stores + retrieves per-integration credentials, encrypted at rest.

    Layout inside the encrypted JSON blob::

        { "<integration_name>": { "api_key": "...", "access_token": "...",
                                   "refresh_token": "...", "expires_at": ... } }
    """

    def __init__(self, path: Path | None = None) -> None:
        self._store = EncryptedJsonStore(path or _creds_path())

    # -- raw blob helpers ------------------------------------------------

    def _load(self) -> dict:
        data = self._store.load(default={})
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self._store.save(data)

    # -- API key ---------------------------------------------------------

    def store_api_key(self, integration_name: str, key: str) -> None:
        data = self._load()
        entry = data.setdefault(integration_name, {})
        entry["api_key"] = key
        self._save(data)
        log.info("[integrations] stored api_key for %s", integration_name)

    def get_api_key(self, integration_name: str) -> str | None:
        return self._load().get(integration_name, {}).get("api_key")

    # -- OAuth2 tokens ---------------------------------------------------

    def store_tokens(self, integration_name: str, tokens: dict) -> None:
        """Persist an OAuth2 token bundle (access/refresh/expires_at)."""
        data = self._load()
        entry = data.setdefault(integration_name, {})
        for k in ("access_token", "refresh_token", "expires_at", "token_type"):
            if k in tokens:
                entry[k] = tokens[k]
        self._save(data)
        log.info("[integrations] stored oauth tokens for %s", integration_name)

    def get_tokens(self, integration_name: str) -> dict | None:
        entry = self._load().get(integration_name, {})
        if "access_token" in entry:
            return entry
        return None

    # -- cleanup ---------------------------------------------------------

    def delete_credentials(self, integration_name: str) -> bool:
        data = self._load()
        if integration_name in data:
            del data[integration_name]
            self._save(data)
            log.info("[integrations] deleted credentials for %s", integration_name)
            return True
        return False

    def has_credentials(self, integration_name: str) -> bool:
        entry = self._load().get(integration_name, {})
        return bool(entry.get("api_key") or entry.get("access_token"))

    # -- OAuth2 flow -----------------------------------------------------

    def build_auth_url(
        self, auth_url: str, client_id: str, redirect_uri: str,
        scopes: list[str], *, extra: dict | None = None,
    ) -> tuple[str, str]:
        """Construct the provider's consent-screen URL + a CSRF state token.

        Task 11: returns ``(url, state)``. ``state`` is a fresh
        ``secrets.token_urlsafe(32)`` embedded in the URL; the caller MUST
        pass the SAME value as ``expected_state`` to ``run_callback_server``
        so the redirect is verified (CSRF protection, RFC 6749 §10.12).
        """
        from urllib.parse import urlencode

        state = secrets.token_urlsafe(_STATE_NBYTES)
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        if extra:
            params.update(extra)
        return f"{auth_url}?{urlencode(params)}", state

    async def exchange_code(
        self, token_url: str, *, client_id: str, client_secret: str,
        code: str, redirect_uri: str,
    ) -> dict:
        """Exchange an authorization code for tokens via httpx.

        Returns the raw token dict on success; raises on HTTP error so the
        caller can report it.
        """
        import httpx

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(token_url, data=payload)
            resp.raise_for_status()
            return resp.json()

    async def refresh_oauth2_token(
        self, integration_name: str, *, token_url: str,
        client_id: str, client_secret: str,
    ) -> str | None:
        """Refresh an access token using the stored refresh_token.

        Persists the new access token and returns it, or None on failure.
        """
        tokens = self.get_tokens(integration_name)
        if not tokens or not tokens.get("refresh_token"):
            return None
        import httpx

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(token_url, data=payload)
                resp.raise_for_status()
                new = resp.json()
        except Exception:
            log.warning("[integrations] token refresh failed for %s",
                        integration_name, exc_info=True)
            return None
        # Preserve the refresh_token if the provider didn't return a new one.
        merged = dict(tokens)
        merged.update(new)
        self.store_tokens(integration_name, merged)
        return merged.get("access_token")

    @staticmethod
    def run_callback_server(
        *,
        expected_state: str,
        redirect_host: str = "127.0.0.1",
        redirect_port: int = 8765,
        redirect_path: str = "/",
        timeout: float = 120.0,
    ) -> str | None:
        """Run a one-shot 127.0.0.1 HTTP server to catch the OAuth redirect.

        Blocks until the provider redirects back to ``redirect_path`` with a
        ``?code=...&state=...`` whose ``state`` matches ``expected_state``,
        or until ``timeout`` elapses. Returns the authorization code, or
        None on timeout. Pure stdlib (http.server) — no extra deps. Call
        inside ``asyncio.to_thread`` so it does not block the loop.

        Security (Task 11, RFC 6749 §10.12):
          * Binds ONLY 127.0.0.1 (never 0.0.0.0 / localhost).
          * Requires GET on ``redirect_path``; other paths -> 404, other
            methods -> 405, and NEITHER ends the wait.
          * ``state`` is compared with ``hmac.compare_digest``; a missing /
            empty / wrong state -> 400 and the wait CONTINUES (a stray or
            forged request must not abort a legitimate pending auth).
          * ``state`` is single-use: after the first valid match the server
            stops; a replay cannot be accepted because the server is gone.
          * The browser response is a STATIC page — no request parameter
            (code / state / error) is ever reflected into the HTML.
          * ``code`` / ``state`` / tokens are never logged; only a provider
            ``error`` key is logged at WARNING, without its value.

        The caller owns ``redirect_port`` and the ``redirect_uri`` given to
        the provider; this server listens on 127.0.0.1 at the same port.
        OAuth providers match ``redirect_uri`` exactly, so the caller must
        register/pass a URI whose host+port match what it binds here.

        Raises ``OAuthPortInUseError`` if ``redirect_port`` is already bound.
        """
        import http.server
        import socket
        from urllib.parse import parse_qs, urlparse

        expected = (expected_state or "").encode("utf-8")
        if not expected:
            raise ValueError("expected_state must be a non-empty string")

        captured: dict[str, str] = {}

        _static_ok = (
            b"<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            b"<title>Jarvis</title></head><body>"
            b"<h2>Authorization received. You can close this window.</h2>"
            b"</body></html>"
        )
        _static_bad = (
            b"<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            b"<title>Jarvis</title></head><body>"
            b"<h2>Invalid or unexpected request.</h2>"
            b"</body></html>"
        )

        class _Handler(http.server.BaseHTTPRequestHandler):
            def _html(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != redirect_path:
                    # Unknown path: 404, do NOT end the wait.
                    self._html(404, _static_bad)
                    return
                qs = parse_qs(parsed.query)
                if "error" in qs:
                    # Provider-reported error. Log the fact (not the value).
                    log.warning("[integrations] oauth provider returned an error")
                    self._html(400, _static_bad)
                    return
                got_state = (qs.get("state", [""])[0] or "").encode("utf-8")
                code = qs.get("code", [""])[0]
                if not code or not got_state or not hmac.compare_digest(
                    got_state, expected
                ):
                    # Missing code or bad/absent state: reject, keep waiting.
                    # No code/state value is logged.
                    log.warning(
                        "[integrations] oauth callback rejected "
                        "(missing code or state mismatch)"
                    )
                    self._html(400, _static_bad)
                    return
                # Valid: capture and signal the server loop to stop.
                captured["code"] = code
                self._html(200, _static_ok)

            def do_POST(self):  # noqa: N802
                self._html(405, _static_bad)

            def do_HEAD(self):  # noqa: N802
                self._html(405, _static_bad)

            def log_message(self, *a):  # silence default stderr logging
                return

        try:
            server = http.server.HTTPServer((redirect_host, redirect_port), _Handler)
        except OSError as e:
            # Port already bound. errno differs by platform: POSIX ->
            # EADDRINUSE (98); Windows -> WSAEADDRINUSE (10048) OR, when the
            # port is held with SO_REUSEADDR by another socket, WSAEACCES
            # (10013, surfaced as PermissionError). Treat all as "in use".
            _in_use = {
                socket.errno.EADDRINUSE,
                getattr(socket.errno, "WSAEADDRINUSE", 10048),
                getattr(socket.errno, "WSAEACCES", 10013),
                10013, 10048,
            }
            if e.errno in _in_use or isinstance(e, PermissionError):
                raise OAuthPortInUseError(
                    f"порт {redirect_port} занят; закройте использующее его "
                    "приложение и повторите"
                ) from e
            raise

        # Serve requests until a valid callback arrives or we pass the
        # deadline. handle_request() honours server.timeout per call; an
        # invalid request returns without populating `captured`, so we loop.
        import time as _time
        server.timeout = timeout
        deadline = _time.monotonic() + timeout
        try:
            while "code" not in captured:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    break
                server.timeout = remaining
                server.handle_request()
        finally:
            server.server_close()
        return captured.get("code")
