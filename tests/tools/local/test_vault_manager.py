"""Tests for vault management tools.

Verifies:
- save/list/remove/unlock/lock lifecycle
- list_saved_sites returns ONLY names
- remove_credential requires confirmation (DESTRUCTIVE)
- No credentials leak into ToolResult
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.registry import EmptyArgs, ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_dpapi():
    """Patch DPAPI to passthrough so tests run on any platform."""
    with patch("jarvis.platform.secrets.dpapi_available", return_value=False):
        yield


@pytest.fixture()
def vault(tmp_path: Path):
    """A fresh PasswordVault pointing to a temp file."""
    from jarvis.security.password_vault import PasswordVault

    return PasswordVault(vault_path=tmp_path / "vault.enc")


# ---------------------------------------------------------------------------
# SaveCredentialTool
# ---------------------------------------------------------------------------


class TestSaveCredentialTool:
    @pytest.mark.asyncio
    async def test_save_credential_success(self, vault):
        from jarvis.tools.local.vault_manager import (
            SaveCredentialArgs,
            SaveCredentialTool,
        )

        vault.create("master123")
        tool = SaveCredentialTool(vault=vault)
        result = await tool.execute(
            SaveCredentialArgs(
                site="steam",
                username="gamer42",
                password="s3cr3t",
                url="https://steampowered.com",
            )
        )

        assert result.success
        assert "steam" in result.output
        # CRITICAL: no credentials in output
        assert "s3cr3t" not in result.output
        assert "gamer42" not in result.output

    @pytest.mark.asyncio
    async def test_save_credential_vault_locked(self, vault):
        from jarvis.tools.local.vault_manager import (
            SaveCredentialArgs,
            SaveCredentialTool,
        )

        # vault is NOT created/unlocked
        tool = SaveCredentialTool(vault=vault)
        result = await tool.execute(
            SaveCredentialArgs(site="steam", username="user", password="s3cr3t")
        )

        assert not result.success
        assert "locked" in result.error.lower()


# ---------------------------------------------------------------------------
# ListSavedSitesTool
# ---------------------------------------------------------------------------


class TestListSavedSitesTool:
    @pytest.mark.asyncio
    async def test_list_sites_only_names(self, vault):
        """list_saved_sites returns ONLY site names, NEVER passwords."""
        from jarvis.tools.local.vault_manager import ListSavedSitesTool

        vault.create("master123")
        vault.add_credential("steam", "user1", password="pass1")
        vault.add_credential("gmail", "user2", password="pass2")

        tool = ListSavedSitesTool(vault=vault)
        result = await tool.execute(EmptyArgs())

        assert result.success
        assert "steam" in result.output
        assert "gmail" in result.output
        # CRITICAL: no passwords in output
        assert "pass1" not in result.output
        assert "pass2" not in result.output
        assert "user1" not in result.output
        assert "user2" not in result.output
        assert "2 sites" in result.output

    @pytest.mark.asyncio
    async def test_list_sites_empty_vault(self, vault):
        from jarvis.tools.local.vault_manager import ListSavedSitesTool

        vault.create("master123")
        tool = ListSavedSitesTool(vault=vault)
        result = await tool.execute(EmptyArgs())

        assert result.success
        assert "No saved credentials" in result.output

    @pytest.mark.asyncio
    async def test_list_sites_vault_locked(self, vault):
        from jarvis.tools.local.vault_manager import ListSavedSitesTool

        tool = ListSavedSitesTool(vault=vault)
        result = await tool.execute(EmptyArgs())

        assert not result.success
        assert "locked" in result.error.lower()


# ---------------------------------------------------------------------------
# RemoveCredentialTool
# ---------------------------------------------------------------------------


class TestRemoveCredentialTool:
    def test_remove_requires_confirmation(self):
        """RemoveCredentialTool is marked DESTRUCTIVE."""
        from jarvis.tools.local.vault_manager import RemoveCredentialTool

        tool = RemoveCredentialTool()
        assert tool.requires_confirmation

    @pytest.mark.asyncio
    async def test_remove_credential_success(self, vault):
        from jarvis.tools.local.vault_manager import (
            RemoveCredentialArgs,
            RemoveCredentialTool,
        )

        vault.create("master123")
        vault.add_credential("steam", "user", password="test_pw")

        tool = RemoveCredentialTool(vault=vault)
        result = await tool.execute(RemoveCredentialArgs(site="steam"))

        assert result.success
        assert "removed" in result.output.lower()
        # Credential should be gone
        assert vault.get_credential("steam") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, vault):
        from jarvis.tools.local.vault_manager import (
            RemoveCredentialArgs,
            RemoveCredentialTool,
        )

        vault.create("master123")
        tool = RemoveCredentialTool(vault=vault)
        result = await tool.execute(RemoveCredentialArgs(site="nonexistent"))

        assert not result.success


# ---------------------------------------------------------------------------
# UnlockVaultTool
# ---------------------------------------------------------------------------


class TestUnlockVaultTool:
    @pytest.mark.asyncio
    async def test_unlock_creates_new_vault(self, vault):
        from jarvis.tools.local.vault_manager import (
            UnlockVaultArgs,
            UnlockVaultTool,
        )

        tool = UnlockVaultTool(vault=vault)
        result = await tool.execute(
            UnlockVaultArgs(master_password="master123")
        )

        assert result.success
        assert "created" in result.output.lower()
        assert vault.is_unlocked

    @pytest.mark.asyncio
    async def test_unlock_existing_vault(self, vault):
        from jarvis.tools.local.vault_manager import (
            UnlockVaultArgs,
            UnlockVaultTool,
        )

        vault.create("master123")
        vault.lock()

        tool = UnlockVaultTool(vault=vault)
        result = await tool.execute(
            UnlockVaultArgs(master_password="master123")
        )

        assert result.success
        assert "unlocked" in result.output.lower()

    @pytest.mark.asyncio
    async def test_unlock_wrong_password(self, vault):
        from jarvis.tools.local.vault_manager import (
            UnlockVaultArgs,
            UnlockVaultTool,
        )

        vault.create("master123")
        vault.lock()

        tool = UnlockVaultTool(vault=vault)
        result = await tool.execute(
            UnlockVaultArgs(master_password="wrong_pw")
        )

        assert not result.success
        assert "wrong" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unlock_empty_password_prompts_ui(self, vault):
        from jarvis.tools.local.vault_manager import (
            UnlockVaultArgs,
            UnlockVaultTool,
        )

        tool = UnlockVaultTool(vault=vault)
        result = await tool.execute(UnlockVaultArgs(master_password=""))

        assert not result.success
        assert "secure input" in result.error.lower()

    @pytest.mark.asyncio
    async def test_already_unlocked(self, vault):
        from jarvis.tools.local.vault_manager import (
            UnlockVaultArgs,
            UnlockVaultTool,
        )

        vault.create("master123")
        # Already unlocked
        tool = UnlockVaultTool(vault=vault)
        result = await tool.execute(
            UnlockVaultArgs(master_password="master123")
        )

        assert result.success
        assert "already unlocked" in result.output.lower()


# ---------------------------------------------------------------------------
# LockVaultTool
# ---------------------------------------------------------------------------


class TestLockVaultTool:
    @pytest.mark.asyncio
    async def test_lock_vault(self, vault):
        from jarvis.tools.local.vault_manager import LockVaultTool

        vault.create("master123")
        tool = LockVaultTool(vault=vault)
        result = await tool.execute(EmptyArgs())

        assert result.success
        assert "locked" in result.output.lower()
        assert not vault.is_unlocked

    @pytest.mark.asyncio
    async def test_lock_already_locked(self, vault):
        from jarvis.tools.local.vault_manager import LockVaultTool

        tool = LockVaultTool(vault=vault)
        result = await tool.execute(EmptyArgs())

        assert result.success
        assert "already locked" in result.output.lower()


# ---------------------------------------------------------------------------
# ChangeVaultPasswordTool
# ---------------------------------------------------------------------------


class TestChangeVaultPasswordTool:
    def test_change_requires_confirmation(self):
        """ChangeVaultPasswordTool is marked DESTRUCTIVE."""
        from jarvis.tools.local.vault_manager import ChangeVaultPasswordTool

        tool = ChangeVaultPasswordTool()
        assert tool.requires_confirmation

    @pytest.mark.asyncio
    async def test_change_password_success(self, vault):
        from jarvis.tools.local.vault_manager import (
            ChangeVaultPasswordArgs,
            ChangeVaultPasswordTool,
        )

        vault.create("old_pass")

        tool = ChangeVaultPasswordTool(vault=vault)
        result = await tool.execute(
            ChangeVaultPasswordArgs(
                old_password="old_pass", new_password="new_pass"
            )
        )

        assert result.success
        assert "changed" in result.output.lower()

        # Verify new password works
        vault.lock()
        assert vault.unlock("new_pass")

    @pytest.mark.asyncio
    async def test_change_password_empty_prompts_ui(self, vault):
        from jarvis.tools.local.vault_manager import (
            ChangeVaultPasswordArgs,
            ChangeVaultPasswordTool,
        )

        tool = ChangeVaultPasswordTool(vault=vault)
        result = await tool.execute(
            ChangeVaultPasswordArgs(old_password="old_pass", new_password="new_pass")
        )

        assert not result.success
        assert "secure input" in result.error.lower()


# ---------------------------------------------------------------------------
# Voice patterns
# ---------------------------------------------------------------------------


class TestVaultManagerVoicePatterns:
    """Spot-check voice patterns across tools."""

    @pytest.mark.parametrize(
        "tool_cls, utterance",
        [
            ("SaveCredentialTool", "save password for steam"),
            ("SaveCredentialTool", "запомни пароль для steam"),
            ("ListSavedSitesTool", "list saved sites"),
            ("ListSavedSitesTool", "какие пароли сохранены"),
            ("RemoveCredentialTool", "remove password for steam"),
            ("RemoveCredentialTool", "удали пароль для steam"),
            ("UnlockVaultTool", "unlock vault"),
            ("UnlockVaultTool", "разблокируй хранилище паролей"),
            ("LockVaultTool", "lock vault"),
            ("LockVaultTool", "заблокируй хранилище паролей"),
        ],
    )
    def test_pattern_matches(self, tool_cls, utterance):
        import jarvis.tools.local.vault_manager as vm

        cls = getattr(vm, tool_cls)
        patterns = getattr(cls, "voice_patterns", ())
        for vp in patterns:
            m = re.match(vp.regex, utterance, re.IGNORECASE)
            if m:
                return  # matched
        pytest.fail(f"No pattern on {tool_cls} matched: {utterance!r}")
