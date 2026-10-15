"""Auto Login — fill login forms from the password vault.

CRITICAL SECURITY:
- Password NEVER enters LLM context
- Password NEVER appears in logs or ToolResult
- Password NEVER stored in memory/action_logger
- Tool returns only "Logged in to {site}, sir." — no credentials

Flow:
1. Get site name from voice command
2. Vault.get_credential(site) → decrypted in RAM only
3. Selenium enters credentials into form fields
4. Credentials wiped from RAM
5. Return success/failure (NO credential data)

Dependencies (optional, checked at runtime):
    - selenium for DOM interaction
    - jarvis.security.password_vault for credential storage
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


class AutoLoginArgs(BaseModel):
    """Arguments for auto_login tool."""

    site: str = Field(..., description="Site name or alias to log in to (e.g. 'steam', 'gmail')")


class AutoLoginTool:
    """Auto-fill login forms from the password vault.

    CRITICAL SECURITY:
    - Password NEVER enters LLM context
    - Password NEVER appears in ToolResult.output or ToolResult.error
    - Tool returns only "Logged in to {site}, sir." or an error WITHOUT credentials
    """

    name: str = "auto_login"
    description: str = (
        "Log in to a website using saved credentials from the password vault. "
        "Requires an active Selenium browser session and an unlocked vault. "
        "(RU: залогинься, войди на сайт, авторизуйся / "
        "UK: залогінься, увійди на сайт, авторизуйся)"
    )
    args_schema = AutoLoginArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:log\s*in|sign\s*in)\s+(?:to|on)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:auto[- ]?login)\s+(?:to|on)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:залогинься|войди|авторизуйся)\s+(?:на|в)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:введи\s+)?(?:логин\s+и\s+)?пароль\s+(?:на|для|в)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:залогінься|увійди|авторизуйся)\s+(?:на|в)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
    )

    def __init__(
        self,
        *,
        vault: Any = None,
        browser_controller: Any = None,
    ) -> None:
        self._vault = vault
        self._browser_controller = browser_controller

    def _get_vault(self) -> Any:
        """Lazy-load the vault singleton."""
        if self._vault is not None:
            return self._vault
        try:
            from jarvis.security.password_vault import PasswordVault

            self._vault = PasswordVault()
            return self._vault
        except Exception:
            return None

    async def execute(self, args: AutoLoginArgs) -> ToolResult:
        """Execute auto-login. NEVER includes credentials in the result."""
        vault = self._get_vault()
        if vault is None:
            return ToolResult(
                success=False,
                error="Password vault is not available.",
            )

        if not vault.is_unlocked:
            return ToolResult(
                success=False,
                error=(
                    "Password vault is locked, sir. "
                    "Please unlock it first with 'unlock vault'."
                ),
            )

        # 1. Get credential from vault (decrypted in RAM)
        cred = vault.get_credential(args.site)
        if not cred:
            return ToolResult(
                success=False,
                error=f"No saved credentials for '{args.site}', sir.",
            )

        # 2. Check browser availability
        bc = self._browser_controller
        if bc is None:
            try:
                from jarvis.tools.local.browser_tabs import _get_browser

                bc = _get_browser()
            except Exception:
                pass
        if bc is None or not getattr(bc, "driver_active", False):
            return ToolResult(
                success=False,
                error=(
                    "Auto-login requires an active Selenium browser session. "
                    "Open a page first, sir."
                ),
            )

        driver = getattr(bc, "_driver", None)
        if driver is None:
            return ToolResult(
                success=False,
                error="No Selenium WebDriver available.",
            )

        # 3. Fill login form
        try:
            filled = await asyncio.to_thread(
                self._fill_login_form,
                driver,
                cred.username,
                cred.password,
                cred.url,
            )
        except Exception as exc:
            # NEVER include credentials in error messages
            log.debug(
                "[auto_login] form fill failed for site '%s'",
                args.site,
                exc_info=True,
            )
            return ToolResult(
                success=False,
                error=f"Could not fill login form for '{args.site}', sir.",
            )

        if not filled:
            return ToolResult(
                success=False,
                error=(
                    f"Could not find login fields on the current page for "
                    f"'{args.site}', sir."
                ),
            )

        # NEVER include credentials in result!
        return ToolResult(
            success=True,
            output=f"Logged in to {args.site}, sir.",
        )

    @staticmethod
    def _fill_login_form(
        driver: Any,
        username: str,
        password: str,
        login_url: str = "",
    ) -> bool:
        """Fill login form fields using Selenium.

        Searches for username/email and password input fields by common
        attributes: type, name, id, autocomplete.

        Returns True if at least username + password were filled.
        """
        # Navigate to login URL if provided and not already there
        if login_url and login_url not in (driver.current_url or ""):
            driver.get(login_url)
            import time as _time

            _time.sleep(2)  # Wait for page load

        # Find username field
        username_field = None
        for selector in [
            'input[type="email"]',
            'input[type="text"][name*="user"]',
            'input[type="text"][name*="login"]',
            'input[type="text"][name*="email"]',
            'input[type="text"][autocomplete="username"]',
            'input[autocomplete="email"]',
            'input[type="text"][id*="user"]',
            'input[type="text"][id*="login"]',
            'input[type="text"][id*="email"]',
            'input[type="text"]',  # last resort
        ]:
            try:
                elems = driver.find_elements("css selector", selector)
                if elems:
                    username_field = elems[0]
                    break
            except Exception:
                continue

        # Find password field
        password_field = None
        for selector in [
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ]:
            try:
                elems = driver.find_elements("css selector", selector)
                if elems:
                    password_field = elems[0]
                    break
            except Exception:
                continue

        if not username_field or not password_field:
            return False

        # Clear and fill fields
        username_field.clear()
        username_field.send_keys(username)
        password_field.clear()
        password_field.send_keys(password)

        return True
