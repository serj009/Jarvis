"""Vault management tools — add/remove/list/unlock/lock credentials.

SECURITY CONTRACT:
- save_credential: password entered via secure UI prompt, NEVER via voice
- list_saved_sites: returns ONLY site names, NEVER passwords
- remove_credential: DESTRUCTIVE, requires confirmation
- unlock_vault: master password via secure UI prompt, NEVER via voice
- lock_vault: safe, no confirmation needed
- change_vault_password: old + new passwords via secure UI prompt

All ToolResult outputs are credential-free. Passwords NEVER appear in
LLM context, logs, ToolResult, or any persistent store.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args schemas
# ---------------------------------------------------------------------------

class SaveCredentialArgs(BaseModel):
    """Arguments for save_credential."""

    site: str = Field(..., description="Site name (e.g. 'steam', 'gmail.com')")
    username: str = Field(..., description="Login username or email")
    password: str = Field(..., description="Login password (entered via secure UI)")
    url: str = Field("", description="Login page URL (optional)")
    notes: str = Field("", description="Optional notes")
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternate names for the site (e.g. ['стім', 'стим'])",
    )


class RemoveCredentialArgs(BaseModel):
    """Arguments for remove_credential."""

    site: str = Field(..., description="Site name to remove")


class UnlockVaultArgs(BaseModel):
    """Arguments for unlock_vault."""

    master_password: str = Field(
        ...,
        description="Master password (entered via secure UI prompt, not voice)",
    )


class CreateVaultArgs(BaseModel):
    """Arguments for initial vault creation."""

    master_password: str = Field(
        ...,
        description="Master password for the new vault (entered via secure UI)",
    )


class ChangeVaultPasswordArgs(BaseModel):
    """Arguments for change_vault_password."""

    old_password: str = Field(..., description="Current master password")
    new_password: str = Field(..., description="New master password")


# ---------------------------------------------------------------------------
# Shared vault accessor
# ---------------------------------------------------------------------------

def _get_vault() -> Any:
    """Get or create the PasswordVault singleton."""
    from jarvis.security.password_vault import PasswordVault

    if not hasattr(_get_vault, "_instance"):
        _get_vault._instance = PasswordVault()
    return _get_vault._instance


def _reset_vault_singleton() -> None:
    """Test hook: clear the singleton so a fresh vault is created."""
    if hasattr(_get_vault, "_instance"):
        del _get_vault._instance


# ---------------------------------------------------------------------------
# SaveCredentialTool
# ---------------------------------------------------------------------------

class SaveCredentialTool:
    """Save login credentials to the vault.

    SECURITY: password should be entered via a secure UI prompt (not voice!).
    Voice command triggers the UI dialog; LLM never sees the actual password.
    """

    name: str = "save_credential"
    description: str = (
        "Save login credentials (username + password) for a website to the "
        "encrypted password vault. Password must be entered via secure UI "
        "prompt, not voice. "
        "(RU: запомни пароль для, сохрани пароль / "
        "UK: запам'ятай пароль для, збережи пароль)"
    )
    args_schema = SaveCredentialArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^save\s+(?:password|credentials?)\s+(?:for|to)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip(), "username": "", "password": ""},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:запомни|сохрани)\s+пароль\s+(?:для|от|на)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip(), "username": "", "password": ""},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:запам['ʼ]?ятай|збережи)\s+пароль\s+(?:для|від|на)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip(), "username": "", "password": ""},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: SaveCredentialArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if not vault.is_unlocked:
            return ToolResult(
                success=False,
                error="Vault is locked. Please unlock it first.",
            )

        if not vault.exists and not vault.is_unlocked:
            return ToolResult(
                success=False,
                error="No vault found. Create one first with 'unlock vault'.",
            )

        try:
            vault.add_credential(
                site=args.site,
                username=args.username,
                password=args.password,
                url=args.url,
                notes=args.notes,
                aliases=args.aliases,
            )
        except Exception as exc:
            log.debug("[vault] save_credential failed", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to save credentials for '{args.site}'.",
            )

        # NEVER include credentials in result
        return ToolResult(
            success=True,
            output=f"Credentials for '{args.site}' saved to vault, sir.",
        )


# ---------------------------------------------------------------------------
# ListSavedSitesTool
# ---------------------------------------------------------------------------

class ListSavedSitesTool:
    """List sites with saved credentials (no passwords shown)."""

    name: str = "list_saved_sites"
    description: str = (
        "List all sites that have saved login credentials in the password "
        "vault. Returns ONLY site names — never passwords. "
        "(RU: какие пароли сохранены, покажи сохранённые сайты / "
        "UK: які паролі збережені, покажи збережені сайти)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:list|show)\s+saved\s+(?:sites|passwords|credentials)$",
            priority=450,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^what\s+passwords?\s+(?:do\s+i\s+have|are\s+saved)$",
            priority=450,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:какие|покажи)\s+(?:пароли|сайты)\s+сохранен[ыо]$",
            priority=450,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:які|покажи)\s+(?:паролі|сайти)\s+збережен[іо]$",
            priority=450,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: EmptyArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if not vault.is_unlocked:
            return ToolResult(
                success=False,
                error="Vault is locked. Please unlock it first.",
            )

        sites = vault.list_sites()
        if not sites:
            return ToolResult(
                success=True,
                output="No saved credentials in the vault, sir.",
            )

        site_list = "\n".join(f"  • {s}" for s in sorted(sites))
        return ToolResult(
            success=True,
            output=f"Saved credentials for {len(sites)} sites:\n{site_list}",
        )


# ---------------------------------------------------------------------------
# RemoveCredentialTool
# ---------------------------------------------------------------------------

class RemoveCredentialTool:
    """Remove saved credentials for a site."""

    name: str = "remove_credential"
    description: str = (
        "Remove saved login credentials for a website from the password vault. "
        "This is DESTRUCTIVE — the credentials will be permanently deleted. "
        "(RU: удали пароль для, забудь пароль / "
        "UK: видали пароль для, забудь пароль)"
    )
    args_schema = RemoveCredentialArgs
    requires_confirmation: bool = True  # DESTRUCTIVE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:remove|delete)\s+(?:password|credentials?)\s+(?:for|from)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:удали|забудь)\s+пароль\s+(?:для|от|на)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:видали|забудь)\s+пароль\s+(?:для|від|на)\s+(.+)$",
            priority=450,
            args=lambda m: {"site": m.group(1).strip()},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: RemoveCredentialArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if not vault.is_unlocked:
            return ToolResult(
                success=False,
                error="Vault is locked. Please unlock it first.",
            )

        removed = vault.remove_credential(args.site)
        if not removed:
            return ToolResult(
                success=False,
                error=f"No saved credentials for '{args.site}' to remove.",
            )

        return ToolResult(
            success=True,
            output=f"Credentials for '{args.site}' removed from vault, sir.",
        )


# ---------------------------------------------------------------------------
# UnlockVaultTool
# ---------------------------------------------------------------------------

class UnlockVaultTool:
    """Unlock the password vault with master password.

    SECURITY: master password should be entered via secure UI prompt.
    If called via voice, prompt user to type it instead.
    """

    name: str = "unlock_vault"
    description: str = (
        "Unlock the password vault with the master password. "
        "If the vault doesn't exist yet, creates a new one. "
        "Master password must be entered via secure UI prompt. "
        "(RU: разблокируй хранилище паролей / "
        "UK: розблокуй сховище паролів)"
    )
    args_schema = UnlockVaultArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^unlock\s+(?:the\s+)?(?:password\s+)?vault$",
            priority=450,
            args=lambda m: {"master_password": ""},
        ),
        # RU
        VoicePattern(
            regex=r"^разблокируй\s+(?:хранилище|сейф)\s+паролей$",
            priority=450,
            args=lambda m: {"master_password": ""},
        ),
        # UK
        VoicePattern(
            regex=r"^розблокуй\s+(?:сховище|сейф)\s+паролів$",
            priority=450,
            args=lambda m: {"master_password": ""},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: UnlockVaultArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if vault.is_unlocked:
            return ToolResult(
                success=True,
                output="Vault is already unlocked, sir.",
            )

        if not args.master_password:
            return ToolResult(
                success=False,
                error=(
                    "Please enter the master password via the secure input "
                    "dialog — not via voice, sir."
                ),
            )

        if not vault.exists:
            # Create new vault
            try:
                vault.create(args.master_password)
            except Exception as exc:
                log.debug("[vault] create failed", exc_info=True)
                return ToolResult(
                    success=False,
                    error="Failed to create the password vault.",
                )
            return ToolResult(
                success=True,
                output="New password vault created and unlocked, sir.",
            )

        # Unlock existing vault
        ok = vault.unlock(args.master_password)
        if not ok:
            return ToolResult(
                success=False,
                error="Wrong master password, sir.",
            )

        return ToolResult(
            success=True,
            output="Password vault unlocked, sir.",
        )


# ---------------------------------------------------------------------------
# LockVaultTool
# ---------------------------------------------------------------------------

class LockVaultTool:
    """Lock the vault, wiping master key from memory."""

    name: str = "lock_vault"
    description: str = (
        "Lock the password vault, wiping the master key from memory. "
        "(RU: заблокируй хранилище паролей / "
        "UK: заблокуй сховище паролів)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^lock\s+(?:the\s+)?(?:password\s+)?vault$",
            priority=450,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^заблокируй\s+(?:хранилище|сейф)\s+паролей$",
            priority=450,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^заблокуй\s+(?:сховище|сейф)\s+паролів$",
            priority=450,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: EmptyArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if not vault.is_unlocked:
            return ToolResult(
                success=True,
                output="Vault is already locked, sir.",
            )

        vault.lock()
        return ToolResult(
            success=True,
            output="Password vault locked, sir. Master key wiped from memory.",
        )


# ---------------------------------------------------------------------------
# ChangeVaultPasswordTool
# ---------------------------------------------------------------------------

class ChangeVaultPasswordTool:
    """Change the master password."""

    name: str = "change_vault_password"
    description: str = (
        "Change the master password for the password vault. "
        "Requires both the old and new passwords via secure UI prompt. "
        "(RU: смени мастер-пароль / UK: зміни мастер-пароль)"
    )
    args_schema = ChangeVaultPasswordArgs
    requires_confirmation: bool = True  # DESTRUCTIVE — wrong old password = locked out

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^change\s+(?:the\s+)?(?:master\s+)?(?:vault\s+)?password$",
            priority=450,
            args=lambda m: {"old_password": "", "new_password": ""},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:смени|поменяй)\s+(?:мастер[- ]?)?пароль\s+(?:хранилища|сейфа)$",
            priority=450,
            args=lambda m: {"old_password": "", "new_password": ""},
        ),
    )

    def __init__(self, *, vault: Any = None) -> None:
        self._vault = vault

    async def execute(self, args: ChangeVaultPasswordArgs) -> ToolResult:
        vault = self._vault or _get_vault()

        if not args.old_password or not args.new_password:
            return ToolResult(
                success=False,
                error=(
                    "Please enter both old and new passwords via the secure "
                    "input dialog — not via voice, sir."
                ),
            )

        try:
            ok = vault.change_master_password(args.old_password, args.new_password)
        except Exception:
            log.debug("[vault] change_master_password failed", exc_info=True)
            return ToolResult(
                success=False,
                error="Failed to change the master password.",
            )

        if not ok:
            return ToolResult(
                success=False,
                error="Wrong current master password, sir.",
            )

        return ToolResult(
            success=True,
            output="Master password changed successfully, sir.",
        )
