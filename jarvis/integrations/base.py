"""BaseIntegration — the contract every online integration implements.

Online integrations are OFF by default. A plugin declares its metadata,
auth requirements, and pip dependencies; the registry discovers it, the
Settings UI lets the user enable + connect it, and only then do its tools
and voice patterns join the live system. With nothing enabled, Jarvis is
exactly as offline as before.

A plugin subclasses ``BaseIntegration`` in a module under
``jarvis/integrations/plugins/`` and sets the class attributes + implements
the abstract methods.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class BaseIntegration(ABC):
    """Abstract base for all online integrations."""

    # -- metadata (override in each plugin) -----------------------------
    name: str = ""                 # machine id, e.g. "google_calendar"
    display_name: str = ""         # UI label, e.g. "Google Calendar"
    category: str = "other"        # productivity | communication | media | ...
    description: str = ""          # one-liner for the Settings card
    icon: str = "\U0001f50c"       # emoji

    # -- auth -----------------------------------------------------------
    # auth_type: "oauth2" | "api_key" | "token" | "none"
    auth_type: str = "none"
    # Each field: {"name": str, "label": str, "type": "password"|"text"}
    auth_fields: list[dict] = []

    # -- dependencies ---------------------------------------------------
    # pip packages checked when the user enables the integration.
    required_packages: list[str] = []

    def __init__(self) -> None:
        self.enabled: bool = False
        self.connected: bool = False

    # -- lifecycle (abstract) -------------------------------------------

    @abstractmethod
    async def connect(self) -> bool:
        """Establish the connection. Called when the user enables + connects.

        Returns True on success. Implementations should set
        ``self.connected`` accordingly and never raise — return False and
        log instead.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear the connection down cleanly. Called on disable."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the connection is currently alive/usable."""

    # -- capabilities (abstract) ----------------------------------------

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """Return Ollama tool-calling schemas contributed by this plugin."""

    @abstractmethod
    async def execute_tool(self, tool_name: str, args: dict) -> str:
        """Run one tool call; return a human-readable result string."""

    @abstractmethod
    def get_voice_patterns(self) -> list[tuple]:
        """Return voice patterns as ``(regex, tool_name, priority)`` tuples
        (EN/RU/UK), matched against the normalized transcription."""

    # -- optional -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Optional feed for the ProactiveEngine (birthdays, events, …).

        Returns a list of dicts the engine can consume, or None. Default
        None so plugins opt in only when they have proactive signals.
        """
        return None

    # -- dependency check (shared helper) -------------------------------

    def missing_packages(self) -> list[str]:
        """Return any ``required_packages`` that are not importable.

        Package names are pip names; the import probe maps the common
        dash/variant forms to a module name heuristically.
        """
        import importlib.util

        missing: list[str] = []
        for pkg in self.required_packages:
            module = _pip_to_module(pkg)
            if importlib.util.find_spec(module) is None:
                missing.append(pkg)
        return missing

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<{type(self).__name__} name={self.name!r} "
            f"enabled={self.enabled} connected={self.connected}>"
        )


def _pip_to_module(pip_name: str) -> str:
    """Best-effort pip-package → import-module name.

    Handles the common cases ("google-api-python-client" → "googleapiclient"
    is special-cased; otherwise dashes become nothing/underscore). Falls
    back to the dash-stripped name.
    """
    special = {
        "google-api-python-client": "googleapiclient",
        "google-auth-oauthlib": "google_auth_oauthlib",
        "google-auth": "google.auth",
        "python-telegram-bot": "telegram",
        "pygithub": "github",
        "PyGithub": "github",
    }
    if pip_name in special:
        return special[pip_name]
    return pip_name.replace("-", "_")
