"""Shared Google OAuth + plumbing for the Google-suite plugins.

The leading underscore keeps the registry's plugin discovery from treating
this module as a plugin (it skips ``_``-prefixed modules).

CRITICAL — lazy imports: plugin modules are imported during registry
discovery, which must succeed even when ``google-api-python-client`` /
``google-auth-oauthlib`` are NOT installed (they live in each plugin's
``required_packages``). So nothing here imports the Google SDKs at module
top level — every heavy import happens inside a method, guarded. Metadata,
tool schemas, and voice patterns work with no packages at all, so a plugin
is always visible and enable-able in Settings; the SDK is only needed once
the user actually connects.

All four Google plugins share one OAuth token, stored under the credential
key ``"google"``, so a single consent flow authorises the whole suite.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# One shared credential bucket for every Google plugin.
GOOGLE_CRED_KEY = "google"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_REQUIRED_PACKAGES = [
    "google-api-python-client",
    "google-auth-oauthlib",
]


class GoogleIntegrationMixin:
    """Mixin providing shared Google auth/state for the suite plugins.

    A concrete Google plugin sets ``name``/``display_name``/``scopes`` and
    implements ``get_tools`` / ``execute_tool`` / ``get_voice_patterns``.
    This mixin handles connect/disconnect/health against the shared token.
    """

    auth_type = "oauth2"
    required_packages = GOOGLE_REQUIRED_PACKAGES
    # Each plugin declares the minimal scopes it needs.
    scopes: list[str] = []

    # Injected by the app/registry when available; tests can set directly.
    auth: object | None = None

    def _get_token(self) -> str | None:
        """Return a valid access token from the shared Google credentials."""
        if self.auth is None:
            return None
        tokens = self.auth.get_tokens(GOOGLE_CRED_KEY)
        if not tokens:
            return None
        return tokens.get("access_token")

    async def connect(self) -> bool:
        """Mark connected if a shared Google token is present.

        The actual OAuth consent flow is driven by the Settings "Connect"
        action (auth.build_auth_url + run_callback_server + exchange_code),
        which stores tokens under GOOGLE_CRED_KEY. Here we simply verify a
        token exists, so enabling a second Google plugin reuses the first's
        consent.
        """
        token = self._get_token()
        self.connected = token is not None
        if not self.connected:
            log.info(
                "[%s] no shared Google token yet — connect Google in Settings",
                getattr(self, "name", "google"),
            )
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return self._get_token() is not None

    def _service(self, api: str, version: str):
        """Build a Google API client, lazily. Raises if SDK/token absent.

        Only called from execute_tool paths, never at import/discovery.
        """
        token = self._get_token()
        if token is None:
            raise RuntimeError("Google is not connected, sir.")
        # Lazy imports — present only when the user installed the extras.
        from google.oauth2.credentials import Credentials  # noqa: E402
        from googleapiclient.discovery import build  # noqa: E402

        creds = Credentials(token=token)
        return build(api, version, credentials=creds, cache_discovery=False)
