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

import logging
import os
from pathlib import Path
from typing import Any

from jarvis.platform.encrypted_store import EncryptedJsonStore

log = logging.getLogger(__name__)


def _creds_path() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis" / "integration_credentials.json"
    return Path.home() / ".jarvis" / "integration_credentials.json"


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
    ) -> str:
        """Construct the provider's consent-screen URL."""
        from urllib.parse import urlencode

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
        }
        if extra:
            params.update(extra)
        return f"{auth_url}?{urlencode(params)}"

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
        redirect_host: str = "localhost", redirect_port: int = 8765,
        *, timeout: float = 120.0,
    ) -> str | None:
        """Run a one-shot localhost HTTP server to catch the OAuth redirect.

        Blocks until the provider redirects back with ``?code=...`` or the
        timeout elapses. Returns the authorization code, or None. Pure
        stdlib (http.server) — no extra deps. Intended to be called inside
        ``asyncio.to_thread`` so it doesn't block the loop.
        """
        import http.server
        from urllib.parse import parse_qs, urlparse

        captured: dict[str, str] = {}

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                if "code" in qs:
                    captured["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Jarvis: you can close this tab.</h2>"
                    b"</body></html>"
                )

            def log_message(self, *a):  # silence the default stderr logging
                return

        server = http.server.HTTPServer((redirect_host, redirect_port), _Handler)
        server.timeout = timeout
        try:
            server.handle_request()  # serves exactly one request
        finally:
            server.server_close()
        return captured.get("code")
