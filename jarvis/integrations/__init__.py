"""Online-integration plugin framework.

Local by default, online by choice: every integration is OFF until the
user enables it in Settings and supplies credentials. See base.py
(BaseIntegration), auth.py (DPAPI credential store + OAuth2 helper), and
registry.py (discovery + enable/disable + collection).
"""

from __future__ import annotations

from jarvis.integrations.auth import IntegrationAuth
from jarvis.integrations.base import BaseIntegration
from jarvis.integrations.registry import IntegrationRegistry

__all__ = ["BaseIntegration", "IntegrationAuth", "IntegrationRegistry"]
